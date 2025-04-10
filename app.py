from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Regexp
import os
import tarfile
import json
from datetime import datetime
import services
import logging
import requests
import re  # Added for regex validation

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/instance/fhir_ig.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FHIR_PACKAGES_DIR'] = os.path.join(app.instance_path, 'fhir_packages')
app.config['API_KEY'] = 'your-api-key-here'  # Hardcoded API key for now; replace with a secure solution

# Ensure directories exist and are writable
instance_path = '/app/instance'
db_path = os.path.join(instance_path, 'fhir_ig.db')
packages_path = app.config['FHIR_PACKAGES_DIR']

logger.debug(f"Instance path: {instance_path}")
logger.debug(f"Database path: {db_path}")
logger.debug(f"Packages path: {packages_path}")

try:
    os.makedirs(instance_path, exist_ok=True)
    os.makedirs(packages_path, exist_ok=True)
    os.chmod(instance_path, 0o777)
    os.chmod(packages_path, 0o777)
    logger.debug(f"Directories created: {os.listdir('/app')}")
    logger.debug(f"Instance contents: {os.listdir(instance_path)}")
except Exception as e:
    logger.error(f"Failed to create directories: {e}")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/fhir_ig.db'
    logger.warning("Falling back to /tmp/fhir_ig.db")

db = SQLAlchemy(app)

class IgImportForm(FlaskForm):
    package_name = StringField('Package Name (e.g., hl7.fhir.us.core)', validators=[
        DataRequired(),
        Regexp(r'^[a-zAZ0-9]+(\.[a-zA-Z0-9]+)+$', message='Invalid package name format.')
    ])
    package_version = StringField('Package Version (e.g., 1.0.0 or current)', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9\.\-]+$', message='Invalid version format.')
    ])
    submit = SubmitField('Fetch & Download IG')

class ProcessedIg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    package_name = db.Column(db.String(128), nullable=False)
    version = db.Column(db.String(32), nullable=False)
    processed_date = db.Column(db.DateTime, nullable=False)
    resource_types_info = db.Column(db.JSON, nullable=False)
    must_support_elements = db.Column(db.JSON, nullable=True)
    examples = db.Column(db.JSON, nullable=True)

# Middleware to check API key
def check_api_key():
    api_key = request.json.get('api_key') if request.is_json else None
    if not api_key:
        logger.error("API key missing in request")
        return jsonify({"status": "error", "message": "API key missing"}), 401
    if api_key != app.config['API_KEY']:
        logger.error(f"Invalid API key provided: {api_key}")
        return jsonify({"status": "error", "message": "Invalid API key"}), 401
    logger.debug("API key validated successfully")
    return None

@app.route('/')
def index():
    return render_template('index.html', site_name='FHIRFLARE IG Toolkit', now=datetime.now())

@app.route('/import-ig', methods=['GET', 'POST'])
def import_ig():
    form = IgImportForm()
    if form.validate_on_submit():
        name = form.package_name.data
        version = form.package_version.data
        try:
            result = services.import_package_and_dependencies(name, version)
            if result['errors'] and not result['downloaded']:
                flash(f"Failed to import {name}#{version}: {result['errors'][0]}", "error")
                return redirect(url_for('import_ig'))
            flash(f"Successfully downloaded {name}#{version} and dependencies!", "success")
            return redirect(url_for('view_igs'))
        except Exception as e:
            flash(f"Error downloading IG: {str(e)}", "error")
    return render_template('import_ig.html', form=form, site_name='FLARE FHIR IG Toolkit', now=datetime.now())

@app.route('/view-igs')
def view_igs():
    igs = ProcessedIg.query.all()
    processed_ids = {(ig.package_name, ig.version) for ig in igs}

    packages = []
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    logger.debug(f"Scanning packages directory: {packages_dir}")
    if os.path.exists(packages_dir):
        for filename in os.listdir(packages_dir):
            if filename.endswith('.tgz'):
                # Split on the last hyphen to separate name and version
                last_hyphen_index = filename.rfind('-')
                if last_hyphen_index != -1 and filename.endswith('.tgz'):
                    name = filename[:last_hyphen_index]
                    version = filename[last_hyphen_index + 1:-4]  # Remove .tgz
                    # Validate that the version looks reasonable (e.g., starts with a digit or is a known keyword)
                    if version[0].isdigit() or version in ('preview', 'current', 'latest'):
                        # Replace underscores with dots to match FHIR package naming convention
                        name = name.replace('_', '.')
                        packages.append({'name': name, 'version': version, 'filename': filename})
                    else:
                        # Fallback: treat as name only, log warning
                        name = filename[:-4]
                        version = ''
                        logger.warning(f"Could not parse version from {filename}, treating as name only")
                        packages.append({'name': name, 'version': version, 'filename': filename})
                else:
                    # Fallback: treat as name only, log warning
                    name = filename[:-4]
                    version = ''
                    logger.warning(f"Could not parse version from {filename}, treating as name only")
                    packages.append({'name': name, 'version': version, 'filename': filename})
        logger.debug(f"Found packages: {packages}")
    else:
        logger.warning(f"Packages directory not found: {packages_dir}")

    # Calculate duplicate_names
    duplicate_names = {}
    for pkg in packages:
        name = pkg['name']
        if name not in duplicate_names:
            duplicate_names[name] = []
        duplicate_names[name].append(pkg)

    # Calculate duplicate_groups
    duplicate_groups = {}
    for name, pkgs in duplicate_names.items():
        if len(pkgs) > 1:  # Only include packages with multiple versions
            duplicate_groups[name] = [pkg['version'] for pkg in pkgs]

    # Precompute group colors
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]

    return render_template('cp_downloaded_igs.html', packages=packages, processed_list=igs, 
                         processed_ids=processed_ids, duplicate_names=duplicate_names, 
                         duplicate_groups=duplicate_groups, group_colors=group_colors,
                         site_name='FLARE FHIR IG Toolkit', now=datetime.now())

@app.route('/push-igs', methods=['GET', 'POST'])
def push_igs():
    igs = ProcessedIg.query.all()
    processed_ids = {(ig.package_name, ig.version) for ig in igs}

    packages = []
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    logger.debug(f"Scanning packages directory: {packages_dir}")
    if os.path.exists(packages_dir):
        for filename in os.listdir(packages_dir):
            if filename.endswith('.tgz'):
                # Split on the last hyphen to separate name and version
                last_hyphen_index = filename.rfind('-')
                if last_hyphen_index != -1 and filename.endswith('.tgz'):
                    name = filename[:last_hyphen_index]
                    version = filename[last_hyphen_index + 1:-4]  # Remove .tgz
                    # Validate that the version looks reasonable (e.g., starts with a digit or is a known keyword)
                    if version[0].isdigit() or version in ('preview', 'current', 'latest'):
                        # Replace underscores with dots to match FHIR package naming convention
                        name = name.replace('_', '.')
                        packages.append({'name': name, 'version': version, 'filename': filename})
                    else:
                        # Fallback: treat as name only, log warning
                        name = filename[:-4]
                        version = ''
                        logger.warning(f"Could not parse version from {filename}, treating as name only")
                        packages.append({'name': name, 'version': version, 'filename': filename})
                else:
                    # Fallback: treat as name only, log warning
                    name = filename[:-4]
                    version = ''
                    logger.warning(f"Could not parse version from {filename}, treating as name only")
                    packages.append({'name': name, 'version': version, 'filename': filename})
        logger.debug(f"Found packages: {packages}")
    else:
        logger.warning(f"Packages directory not found: {packages_dir}")

    # Calculate duplicate_names
    duplicate_names = {}
    for pkg in packages:
        name = pkg['name']
        if name not in duplicate_names:
            duplicate_names[name] = []
        duplicate_names[name].append(pkg)

    # Calculate duplicate_groups
    duplicate_groups = {}
    for name, pkgs in duplicate_names.items():
        if len(pkgs) > 1:  # Only include packages with multiple versions
            duplicate_groups[name] = [pkg['version'] for pkg in pkgs]

    # Precompute group colors
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]

    return render_template('cp_push_igs.html', packages=packages, processed_list=igs, 
                         processed_ids=processed_ids, duplicate_names=duplicate_names, 
                         duplicate_groups=duplicate_groups, group_colors=group_colors,
                         site_name='FLARE FHIR IG Toolkit', now=datetime.now(),
                         api_key=app.config['API_KEY'])  # Pass the API key to the template

@app.route('/process-igs', methods=['POST'])
def process_ig():
    filename = request.form.get('filename')
    if not filename or not filename.endswith('.tgz'):
        flash("Invalid package file.", "error")
        return redirect(url_for('view_igs'))
    
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
    if not os.path.exists(tgz_path):
        flash(f"Package file not found: {filename}", "error")
        return redirect(url_for('view_igs'))
    
    try:
        # Parse name and version from filename
        last_hyphen_index = filename.rfind('-')
        if last_hyphen_index != -1 and filename.endswith('.tgz'):
            name = filename[:last_hyphen_index]
            version = filename[last_hyphen_index + 1:-4]
            # Replace underscores with dots to match FHIR package naming convention
            name = name.replace('_', '.')
        else:
            name = filename[:-4]
            version = ''
            logger.warning(f"Could not parse version from {filename} during processing")
        package_info = services.process_package_file(tgz_path)
        processed_ig = ProcessedIg(
            package_name=name,
            version=version,
            processed_date=datetime.now(),
            resource_types_info=package_info['resource_types_info'],
            must_support_elements=package_info.get('must_support_elements'),
            examples=package_info.get('examples')
        )
        db.session.add(processed_ig)
        db.session.commit()
        flash(f"Successfully processed {name}#{version}!", "success")
    except Exception as e:
        flash(f"Error processing IG: {str(e)}", "error")
    return redirect(url_for('view_igs'))

@app.route('/delete-ig', methods=['POST'])
def delete_ig():
    filename = request.form.get('filename')
    if not filename or not filename.endswith('.tgz'):
        flash("Invalid package file.", "error")
        return redirect(url_for('view_igs'))
    
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
    if os.path.exists(tgz_path):
        try:
            os.remove(tgz_path)
            flash(f"Deleted {filename}", "success")
        except Exception as e:
            flash(f"Error deleting {filename}: {str(e)}", "error")
    else:
        flash(f"File not found: {filename}", "error")
    return redirect(url_for('view_igs'))

@app.route('/unload-ig', methods=['POST'])
def unload_ig():
    ig_id = request.form.get('ig_id')
    if not ig_id:
        flash("Invalid package ID.", "error")
        return redirect(url_for('view_igs'))
    
    processed_ig = ProcessedIg.query.get(ig_id)
    if processed_ig:
        try:
            db.session.delete(processed_ig)
            db.session.commit()
            flash(f"Unloaded {processed_ig.package_name}#{processed_ig.version}", "success")
        except Exception as e:
            flash(f"Error unloading package: {str(e)}", "error")
    else:
        flash(f"Package not found with ID: {ig_id}", "error")
    return redirect(url_for('view_igs'))

@app.route('/view-ig/<int:processed_ig_id>')
def view_ig(processed_ig_id):
    processed_ig = ProcessedIg.query.get_or_404(processed_ig_id)
    profile_list = [t for t in processed_ig.resource_types_info if t.get('is_profile')]
    base_list = [t for t in processed_ig.resource_types_info if not t.get('is_profile')]
    examples_by_type = processed_ig.examples or {}
    return render_template('cp_view_processed_ig.html', title=f"View {processed_ig.package_name}#{processed_ig.version}",
                          processed_ig=processed_ig, profile_list=profile_list, base_list=base_list,
                          examples_by_type=examples_by_type, site_name='FLARE FHIR IG Toolkit', now=datetime.now())

@app.route('/get-structure')
def get_structure_definition():
    package_name = request.args.get('package_name')
    package_version = request.args.get('package_version')
    resource_identifier = request.args.get('resource_type')
    if not all([package_name, package_version, resource_identifier]):
        return jsonify({"error": "Missing query parameters"}), 400
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], services._construct_tgz_filename(package_name, package_version))
    if not os.path.exists(tgz_path):
        return jsonify({"error": f"Package file not found: {tgz_path}"}), 404
    sd_data, _ = services.find_and_extract_sd(tgz_path, resource_identifier)
    if sd_data is None:
        return jsonify({"error": f"SD for '{resource_identifier}' not found."}), 404
    elements = sd_data.get('snapshot', {}).get('element', []) or sd_data.get('differential', {}).get('element', [])
    processed_ig = ProcessedIg.query.filter_by(package_name=package_name, version=package_version).first()
    must_support_paths = processed_ig.must_support_elements.get(resource_identifier, []) if processed_ig else []
    return jsonify({"elements": elements, "must_support_paths": must_support_paths})

@app.route('/get-example')
def get_example_content():
    package_name = request.args.get('package_name')
    package_version = request.args.get('package_version')
    example_member_path = request.args.get('filename')
    if not all([package_name, package_version, example_member_path]):
        return jsonify({"error": "Missing query parameters"}), 400
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], services._construct_tgz_filename(package_name, package_version))
    if not os.path.exists(tgz_path):
        return jsonify({"error": f"Package file not found: {tgz_path}"}), 404
    if not example_member_path.startswith('package/') or '..' in example_member_path:
        return jsonify({"error": "Invalid example file path."}), 400
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            try:
                example_member = tar.getmember(example_member_path)
            except KeyError:
                return jsonify({"error": f"Example file '{example_member_path}' not found."}), 404
            with tar.extractfile(example_member) as example_fileobj:
                content_bytes = example_fileobj.read()
            return content_bytes.decode('utf-8-sig')
    except tarfile.TarError as e:
        return jsonify({"error": f"Error reading {tgz_path}: {e}"}), 500

# API Endpoint: Import IG Package
@app.route('/api/import-ig', methods=['POST'])
def api_import_ig():
    # Check API key
    auth_error = check_api_key()
    if auth_error:
        return auth_error

    # Validate request
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')

    if not package_name or not version:
        return jsonify({"status": "error", "message": "Missing package_name or version"}), 400

    # Validate package name and version format using re
    if not (isinstance(package_name, str) and isinstance(version, str) and 
            re.match(r'^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$', package_name) and 
            re.match(r'^[a-zA-Z0-9\.\-]+$', version)):
        return jsonify({"status": "error", "message": "Invalid package name or version format"}), 400

    try:
        # Import package and dependencies
        result = services.import_package_and_dependencies(package_name, version)
        if result['errors'] and not result['downloaded']:
            return jsonify({"status": "error", "message": f"Failed to import {package_name}#{version}: {result['errors'][0]}"}), 500

        # Check for duplicates
        packages = []
        packages_dir = app.config['FHIR_PACKAGES_DIR']
        if os.path.exists(packages_dir):
            for filename in os.listdir(packages_dir):
                if filename.endswith('.tgz'):
                    # Split on the last hyphen to separate name and version
                    last_hyphen_index = filename.rfind('-')
                    if last_hyphen_index != -1 and filename.endswith('.tgz'):
                        name = filename[:last_hyphen_index]
                        version = filename[last_hyphen_index + 1:-4]
                        # Replace underscores with dots to match FHIR package naming convention
                        name = name.replace('_', '.')
                        if version[0].isdigit() or version in ('preview', 'current', 'latest'):
                            packages.append({'name': name, 'version': version, 'filename': filename})
                        else:
                            name = filename[:-4]
                            version = ''
                            packages.append({'name': name, 'version': version, 'filename': filename})
                    else:
                        name = filename[:-4]
                        version = ''
                        packages.append({'name': name, 'version': version, 'filename': filename})

        # Calculate duplicates
        duplicate_names = {}
        for pkg in packages:
            name = pkg['name']
            if name not in duplicate_names:
                duplicate_names[name] = []
            duplicate_names[name].append(pkg)

        duplicates = []
        for name, pkgs in duplicate_names.items():
            if len(pkgs) > 1:
                versions = [pkg['version'] for pkg in pkgs]
                duplicates.append(f"{name} (exists as {', '.join(versions)})")

        # Deduplicate dependencies
        seen = set()
        unique_dependencies = []
        for dep in result.get('dependencies', []):
            dep_str = f"{dep['name']}#{dep['version']}"
            if dep_str not in seen:
                seen.add(dep_str)
                unique_dependencies.append(dep_str)

        # Prepare response
        response = {
            "status": "success",
            "message": "Package imported successfully",
            "package_name": package_name,
            "version": version,
            "dependencies": unique_dependencies,
            "duplicates": duplicates
        }
        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error in api_import_ig: {str(e)}")
        return jsonify({"status": "error", "message": f"Error importing package: {str(e)}"}), 500

# API Endpoint: Push IG to FHIR Server with Streaming
@app.route('/api/push-ig', methods=['POST'])
def api_push_ig():
    # Check API key
    auth_error = check_api_key()
    if auth_error:
        return auth_error

    # Validate request
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')
    fhir_server_url = data.get('fhir_server_url')
    include_dependencies = data.get('include_dependencies', True)

    if not all([package_name, version, fhir_server_url]):
        return jsonify({"status": "error", "message": "Missing package_name, version, or fhir_server_url"}), 400

    # Validate package name and version format using re
    if not (isinstance(package_name, str) and isinstance(version, str) and 
            re.match(r'^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$', package_name) and 
            re.match(r'^[a-zA-Z0-9\.\-]+$', version)):
        return jsonify({"status": "error", "message": "Invalid package name or version format"}), 400

    # Check if package exists
    tgz_filename = services._construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], tgz_filename)
    if not os.path.exists(tgz_path):
        return jsonify({"status": "error", "message": f"Package not found: {package_name}#{version}"}), 404

    def generate_stream():
        try:
            # Start message
            yield json.dumps({"type": "start", "message": f"Starting push for {package_name}#{version}..."}) + "\n"

            # Extract resources from the package
            resources = []
            with tarfile.open(tgz_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.startswith('package/') and member.name.endswith('.json'):
                        with tar.extractfile(member) as f:
                            resource_data = json.load(f)
                            if 'resourceType' in resource_data:
                                resources.append(resource_data)

            # If include_dependencies is True, find and include dependencies
            pushed_packages = [f"{package_name}#{version}"]
            if include_dependencies:
                yield json.dumps({"type": "progress", "message": "Processing dependencies..."}) + "\n"
                # Re-import to get dependencies (simulating dependency resolution)
                import_result = services.import_package_and_dependencies(package_name, version)
                dependencies = import_result.get('dependencies', [])
                for dep in dependencies:
                    dep_name = dep['name']
                    dep_version = dep['version']
                    dep_tgz_filename = services._construct_tgz_filename(dep_name, dep_version)
                    dep_tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], dep_tgz_filename)
                    if os.path.exists(dep_tgz_path):
                        with tarfile.open(dep_tgz_path, "r:gz") as tar:
                            for member in tar.getmembers():
                                if member.name.startswith('package/') and member.name.endswith('.json'):
                                    with tar.extractfile(member) as f:
                                        resource_data = json.load(f)
                                        if 'resourceType' in resource_data:
                                            resources.append(resource_data)
                        pushed_packages.append(f"{dep_name}#{dep_version}")
                        yield json.dumps({"type": "progress", "message": f"Added dependency {dep_name}#{dep_version}"}) + "\n"
                    else:
                        yield json.dumps({"type": "warning", "message": f"Dependency {dep_name}#{dep_version} not found, skipping"}) + "\n"

            # Push resources to FHIR server
            server_response = []
            success_count = 0
            failure_count = 0
            total_resources = len(resources)
            yield json.dumps({"type": "progress", "message": f"Found {total_resources} resources to upload"}) + "\n"

            for i, resource in enumerate(resources, 1):
                resource_type = resource.get('resourceType')
                resource_id = resource.get('id')
                if not resource_type or not resource_id:
                    yield json.dumps({"type": "warning", "message": f"Skipping invalid resource at index {i}"}) + "\n"
                    failure_count += 1
                    continue

                # Construct the FHIR server URL for the resource
                resource_url = f"{fhir_server_url.rstrip('/')}/{resource_type}/{resource_id}"
                yield json.dumps({"type": "progress", "message": f"Uploading {resource_type}/{resource_id} ({i}/{total_resources})..."}) + "\n"

                try:
                    response = requests.put(resource_url, json=resource, headers={'Content-Type': 'application/fhir+json'})
                    response.raise_for_status()
                    server_response.append(f"Uploaded {resource_type}/{resource_id} successfully")
                    yield json.dumps({"type": "success", "message": f"Uploaded {resource_type}/{resource_id} successfully"}) + "\n"
                    success_count += 1
                except requests.exceptions.RequestException as e:
                    error_msg = f"Failed to upload {resource_type}/{resource_id}: {str(e)}"
                    server_response.append(error_msg)
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1

            # Final summary
            summary = {
                "status": "success" if failure_count == 0 else "partial",
                "message": f"Push completed: {success_count} resources uploaded, {failure_count} failed",
                "package_name": package_name,
                "version": version,
                "pushed_packages": pushed_packages,
                "server_response": "; ".join(server_response) if server_response else "No resources uploaded",
                "success_count": success_count,
                "failure_count": failure_count
            }
            yield json.dumps({"type": "complete", "data": summary}) + "\n"

        except Exception as e:
            logger.error(f"Error in api_push_ig: {str(e)}")
            error_response = {
                "status": "error",
                "message": f"Error pushing package: {str(e)}"
            }
            yield json.dumps({"type": "error", "message": error_response["message"]}) + "\n"
            yield json.dumps({"type": "complete", "data": error_response}) + "\n"

    return Response(generate_stream(), mimetype='application/x-ndjson')

with app.app_context():
    logger.debug(f"Creating database at: {app.config['SQLALCHEMY_DATABASE_URI']}")
    db.create_all()
    logger.debug("Database initialization complete")

# Add route to serve favicon
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

if __name__ == '__main__':
    app.run(debug=True)