# app.py
import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
import tarfile
import json
import logging
import requests
import re
import services
from forms import IgImportForm, ValidationForm

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# --- Configuration ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-fallback-secret-key-here')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:////app/instance/fhir_ig.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FHIR_PACKAGES_DIR'] = '/app/instance/fhir_packages'
app.config['API_KEY'] = os.environ.get('API_KEY', 'your-fallback-api-key-here')
app.config['VALIDATE_IMPOSED_PROFILES'] = True
app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = True

# Ensure directories exist and are writable
instance_path = '/app/instance'
packages_path = app.config['FHIR_PACKAGES_DIR']

logger.debug(f"Instance path configuration: {instance_path}")
logger.debug(f"Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
logger.debug(f"Packages path: {packages_path}")

try:
    instance_folder_path = app.instance_path
    logger.debug(f"Flask instance folder path: {instance_folder_path}")
    os.makedirs(instance_folder_path, exist_ok=True)
    os.makedirs(packages_path, exist_ok=True)
    logger.debug(f"Directories created/verified: Instance: {instance_folder_path}, Packages: {packages_path}")
except Exception as e:
    logger.error(f"Failed to create/verify directories: {e}", exc_info=True)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

# --- Database Model ---
class ProcessedIg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    package_name = db.Column(db.String(128), nullable=False)
    version = db.Column(db.String(64), nullable=False)
    processed_date = db.Column(db.DateTime, nullable=False)
    resource_types_info = db.Column(db.JSON, nullable=False)
    must_support_elements = db.Column(db.JSON, nullable=True)
    examples = db.Column(db.JSON, nullable=True)
    complies_with_profiles = db.Column(db.JSON, nullable=True)
    imposed_profiles = db.Column(db.JSON, nullable=True)
    optional_usage_elements = db.Column(db.JSON, nullable=True)

    __table_args__ = (db.UniqueConstraint('package_name', 'version', name='uq_package_version'),)

# --- API Key Middleware ---
def check_api_key():
    api_key = request.headers.get('X-API-Key')
    if not api_key and request.is_json:
        api_key = request.json.get('api_key')
    if not api_key:
        logger.error("API key missing in request")
        return jsonify({"status": "error", "message": "API key missing"}), 401
    if api_key != app.config['API_KEY']:
        logger.error("Invalid API key provided.")
        return jsonify({"status": "error", "message": "Invalid API key"}), 401
    logger.debug("API key validated successfully")
    return None

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html', site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

@app.route('/import-ig', methods=['GET', 'POST'])
def import_ig():
    form = IgImportForm()
    if form.validate_on_submit():
        name = form.package_name.data
        version = form.package_version.data
        dependency_mode = form.dependency_mode.data
        try:
            result = services.import_package_and_dependencies(name, version, dependency_mode=dependency_mode)
            if result['errors'] and not result['downloaded']:
                error_msg = result['errors'][0]
                simplified_msg = error_msg
                if "HTTP error" in error_msg and "404" in error_msg:
                    simplified_msg = "Package not found on registry (404). Check name and version."
                elif "HTTP error" in error_msg:
                    simplified_msg = f"Registry error: {error_msg.split(': ', 1)[-1]}"
                elif "Connection error" in error_msg:
                    simplified_msg = "Could not connect to the FHIR package registry."
                flash(f"Failed to import {name}#{version}: {simplified_msg}", "error")
                logger.error(f"Import failed critically for {name}#{version}: {error_msg}")
            else:
                if result['errors']:
                    flash(f"Partially imported {name}#{version} with errors during dependency processing. Check logs.", "warning")
                    for err in result['errors']: logger.warning(f"Import warning for {name}#{version}: {err}")
                else:
                    flash(f"Successfully downloaded {name}#{version} and dependencies! Mode: {dependency_mode}", "success")
                return redirect(url_for('view_igs'))
        except Exception as e:
            logger.error(f"Unexpected error during IG import: {str(e)}", exc_info=True)
            flash(f"An unexpected error occurred downloading the IG: {str(e)}", "error")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", "danger")
    return render_template('import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

def list_downloaded_packages(packages_dir):
    packages = []
    errors = []
    duplicate_groups = {}
    logger.debug(f"Scanning packages directory: {packages_dir}")
    if not os.path.exists(packages_dir):
        logger.warning(f"Packages directory not found: {packages_dir}")
        return packages, errors, duplicate_groups
    for filename in os.listdir(packages_dir):
        if filename.endswith('.tgz'):
            full_path = os.path.join(packages_dir, filename)
            name = filename[:-4]
            version = ''
            parsed_name, parsed_version = services.parse_package_filename(filename)
            if parsed_name:
                name = parsed_name
                version = parsed_version
            else:
                logger.warning(f"Could not parse version from {filename}, using default name.")
                errors.append(f"Could not parse {filename}")
            try:
                with tarfile.open(full_path, "r:gz") as tar:
                    pkg_json_member = tar.getmember("package/package.json")
                    fileobj = tar.extractfile(pkg_json_member)
                    if fileobj:
                        pkg_data = json.loads(fileobj.read().decode('utf-8-sig'))
                        name = pkg_data.get('name', name)
                        version = pkg_data.get('version', version)
                        fileobj.close()
            except (KeyError, tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Could not read package.json from {filename}: {e}")
                errors.append(f"Error reading {filename}: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error reading package.json from {filename}: {e}", exc_info=True)
                errors.append(f"Unexpected error for {filename}: {str(e)}")
            if name and version:
                packages.append({'name': name, 'version': version, 'filename': filename})
            else:
                logger.warning(f"Skipping package {filename} due to invalid name ('{name}') or version ('{version}')")
                errors.append(f"Invalid package {filename}: name='{name}', version='{version}'")
    # Build duplicate_groups
    name_counts = {}
    for pkg in packages:
        name = pkg['name']
        name_counts[name] = name_counts.get(name, 0) + 1
    for name, count in name_counts.items():
        if count > 1:
            duplicate_groups[name] = sorted([p['version'] for p in packages if p['name'] == name])
    logger.debug(f"Found packages: {packages}")
    logger.debug(f"Errors during package listing: {errors}")
    logger.debug(f"Duplicate groups: {duplicate_groups}")
    return packages, errors, duplicate_groups

@app.route('/view-igs')
def view_igs():
    form = FlaskForm()
    processed_igs = ProcessedIg.query.order_by(ProcessedIg.package_name, ProcessedIg.version).all()
    processed_ids = {(ig.package_name, ig.version) for ig in processed_igs}
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    packages, errors, duplicate_groups = list_downloaded_packages(packages_dir)
    if errors:
        flash(f"Warning: Errors encountered while listing packages: {', '.join(errors)}", "warning")
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger', 'bg-secondary']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]
    return render_template('cp_downloaded_igs.html', form=form, packages=packages,
                           processed_list=processed_igs, processed_ids=processed_ids,
                           duplicate_groups=duplicate_groups, group_colors=group_colors,
                           site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(),
                           config=app.config)

@app.route('/push-igs', methods=['GET'])
def push_igs():
    form = FlaskForm()
    processed_igs = ProcessedIg.query.order_by(ProcessedIg.package_name, ProcessedIg.version).all()
    processed_ids = {(ig.package_name, ig.version) for ig in processed_igs}
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    packages, errors, duplicate_groups = list_downloaded_packages(packages_dir)
    if errors:
        flash(f"Warning: Errors encountered while listing packages: {', '.join(errors)}", "warning")
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger', 'bg-secondary']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]
    return render_template('cp_push_igs.html', form=form, packages=packages,
                           processed_list=processed_igs, processed_ids=processed_ids,
                           duplicate_groups=duplicate_groups, group_colors=group_colors,
                           site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(),
                           api_key=app.config['API_KEY'], config=app.config)

@app.route('/process-igs', methods=['POST'])
def process_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        filename = request.form.get('filename')
        if not filename or not filename.endswith('.tgz'):
            flash("Invalid package file selected.", "error")
            return redirect(url_for('view_igs'))
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        if not os.path.exists(tgz_path):
            flash(f"Package file not found: {filename}", "error")
            return redirect(url_for('view_igs'))
        name, version = services.parse_package_filename(filename)
        if not name:
            name = filename[:-4].replace('_', '.')
            version = 'unknown'
            logger.warning(f"Using fallback naming for {filename} -> {name}#{version}")
        try:
            logger.info(f"Starting processing for {name}#{version} from file {filename}")
            package_info = services.process_package_file(tgz_path)
            optional_usage_dict = {
                info['name']: True
                for info in package_info.get('resource_types_info', [])
                if info.get('optional_usage')
            }
            logger.debug(f"Optional usage elements identified: {optional_usage_dict}")
            existing_ig = ProcessedIg.query.filter_by(package_name=name, version=version).first()
            if existing_ig:
                logger.info(f"Updating existing processed record for {name}#{version}")
                existing_ig.processed_date = datetime.datetime.now(tz=datetime.timezone.utc)
                existing_ig.resource_types_info = package_info.get('resource_types_info', [])
                existing_ig.must_support_elements = package_info.get('must_support_elements')
                existing_ig.examples = package_info.get('examples')
                existing_ig.complies_with_profiles = package_info.get('complies_with_profiles', [])
                existing_ig.imposed_profiles = package_info.get('imposed_profiles', [])
                existing_ig.optional_usage_elements = optional_usage_dict
            else:
                logger.info(f"Creating new processed record for {name}#{version}")
                processed_ig = ProcessedIg(
                    package_name=name,
                    version=version,
                    processed_date=datetime.datetime.now(tz=datetime.timezone.utc),
                    resource_types_info=package_info.get('resource_types_info', []),
                    must_support_elements=package_info.get('must_support_elements'),
                    examples=package_info.get('examples'),
                    complies_with_profiles=package_info.get('complies_with_profiles', []),
                    imposed_profiles=package_info.get('imposed_profiles', []),
                    optional_usage_elements=optional_usage_dict
                )
                db.session.add(processed_ig)
            db.session.commit()
            flash(f"Successfully processed {name}#{version}!", "success")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error processing IG {filename}: {str(e)}", exc_info=True)
            flash(f"Error processing IG '{filename}': {str(e)}", "error")
    else:
        logger.warning(f"Form validation failed for process-igs: {form.errors}")
        flash("CSRF token missing or invalid, or other form error.", "error")
    return redirect(url_for('view_igs'))

@app.route('/delete-ig', methods=['POST'])
def delete_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        filename = request.form.get('filename')
        if not filename or not filename.endswith('.tgz'):
            flash("Invalid package file specified.", "error")
            return redirect(url_for('view_igs'))
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        metadata_path = tgz_path.replace('.tgz', '.metadata.json')
        deleted_files = []
        errors = []
        if os.path.exists(tgz_path):
            try:
                os.remove(tgz_path)
                deleted_files.append(filename)
                logger.info(f"Deleted package file: {tgz_path}")
            except OSError as e:
                errors.append(f"Could not delete {filename}: {e}")
                logger.error(f"Error deleting {tgz_path}: {e}")
        else:
            flash(f"Package file not found: {filename}", "warning")
        if os.path.exists(metadata_path):
            try:
                os.remove(metadata_path)
                deleted_files.append(os.path.basename(metadata_path))
                logger.info(f"Deleted metadata file: {metadata_path}")
            except OSError as e:
                errors.append(f"Could not delete metadata for {filename}: {e}")
                logger.error(f"Error deleting {metadata_path}: {e}")
        if errors:
            for error in errors: flash(error, "error")
        elif deleted_files:
            flash(f"Deleted: {', '.join(deleted_files)}", "success")
        else:
            flash("No files found to delete.", "info")
    else:
        logger.warning(f"Form validation failed for delete-ig: {form.errors}")
        flash("CSRF token missing or invalid.", "error")
    return redirect(url_for('view_igs'))

@app.route('/unload-ig', methods=['POST'])
def unload_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        ig_id = request.form.get('ig_id')
        try:
            ig_id_int = int(ig_id)
            processed_ig = db.session.get(ProcessedIg, ig_id_int)
            if processed_ig:
                try:
                    pkg_name = processed_ig.package_name
                    pkg_version = processed_ig.version
                    db.session.delete(processed_ig)
                    db.session.commit()
                    flash(f"Unloaded processed data for {pkg_name}#{pkg_version}", "success")
                    logger.info(f"Unloaded DB record for {pkg_name}#{pkg_version} (ID: {ig_id_int})")
                except Exception as e:
                    db.session.rollback()
                    flash(f"Error unloading package data: {str(e)}", "error")
                    logger.error(f"Error deleting ProcessedIg record ID {ig_id_int}: {e}", exc_info=True)
            else:
                flash(f"Processed package data not found with ID: {ig_id}", "error")
                logger.warning(f"Attempted to unload non-existent ProcessedIg record ID: {ig_id}")
        except ValueError:
            flash("Invalid package ID provided.", "error")
            logger.warning(f"Invalid ID format received for unload-ig: {ig_id}")
        except Exception as e:
            flash(f"An unexpected error occurred during unload: {str(e)}", "error")
            logger.error(f"Unexpected error in unload_ig for ID {ig_id}: {e}", exc_info=True)
    else:
        logger.warning(f"Form validation failed for unload-ig: {form.errors}")
        flash("CSRF token missing or invalid.", "error")
    return redirect(url_for('view_igs'))

@app.route('/view-ig/<int:processed_ig_id>')
def view_ig(processed_ig_id):
    processed_ig = db.session.get(ProcessedIg, processed_ig_id)
    if not processed_ig:
        flash(f"Processed IG with ID {processed_ig_id} not found.", "error")
        return redirect(url_for('view_igs'))
    profile_list = [t for t in processed_ig.resource_types_info if t.get('is_profile')]
    base_list = [t for t in processed_ig.resource_types_info if not t.get('is_profile')]
    examples_by_type = processed_ig.examples or {}
    optional_usage_elements = processed_ig.optional_usage_elements or {}
    logger.debug(f"Optional usage elements for {processed_ig.package_name}#{processed_ig.version}: {optional_usage_elements}")
    complies_with_profiles = processed_ig.complies_with_profiles or []
    imposed_profiles = processed_ig.imposed_profiles or []
    return render_template('cp_view_processed_ig.html',
                           title=f"View {processed_ig.package_name}#{processed_ig.version}",
                           processed_ig=processed_ig,
                           profile_list=profile_list,
                           base_list=base_list,
                           examples_by_type=examples_by_type,
                           site_name='FHIRFLARE IG Toolkit',
                           now=datetime.datetime.now(),
                           complies_with_profiles=complies_with_profiles,
                           imposed_profiles=imposed_profiles,
                           optional_usage_elements=optional_usage_elements,
                           config=current_app.config)

@app.route('/get-structure')
def get_structure_definition():
    package_name = request.args.get('package_name')
    package_version = request.args.get('package_version')
    resource_identifier = request.args.get('resource_type')
    if not all([package_name, package_version, resource_identifier]):
        logger.warning("get_structure_definition: Missing query parameters.")
        return jsonify({"error": "Missing required query parameters: package_name, package_version, resource_type"}), 400
    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir:
        logger.error("FHIR_PACKAGES_DIR not configured.")
        return jsonify({"error": "Server configuration error: Package directory not set."}), 500
    tgz_filename = services.construct_tgz_filename(package_name, package_version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    sd_data = None
    fallback_used = False
    source_package_id = f"{package_name}#{package_version}"
    logger.debug(f"Attempting to find SD for '{resource_identifier}' in {tgz_filename}")
    if os.path.exists(tgz_path):
        try:
            sd_data, _ = services.find_and_extract_sd(tgz_path, resource_identifier)
        except Exception as e:
            logger.error(f"Error extracting SD for '{resource_identifier}' from {tgz_path}: {e}", exc_info=True)
    else:
        logger.warning(f"Package file not found: {tgz_path}")
    if sd_data is None:
        logger.info(f"SD for '{resource_identifier}' not found in {source_package_id}. Attempting fallback to {services.CANONICAL_PACKAGE_ID}.")
        core_package_name, core_package_version = services.CANONICAL_PACKAGE
        core_tgz_filename = services.construct_tgz_filename(core_package_name, core_package_version)
        core_tgz_path = os.path.join(packages_dir, core_tgz_filename)
        if not os.path.exists(core_tgz_path):
            logger.warning(f"Core package {services.CANONICAL_PACKAGE_ID} not found locally, attempting download.")
            try:
                result = services.import_package_and_dependencies(core_package_name, core_package_version, dependency_mode='direct')
                if result['errors'] and not result['downloaded']:
                    err_msg = f"Failed to download fallback core package {services.CANONICAL_PACKAGE_ID}: {result['errors'][0]}"
                    logger.error(err_msg)
                    return jsonify({"error": f"SD for '{resource_identifier}' not found in primary package, and failed to download core package: {result['errors'][0]}"}), 500
                elif not os.path.exists(core_tgz_path):
                    err_msg = f"Core package download reported success but file {core_tgz_filename} still not found."
                    logger.error(err_msg)
                    return jsonify({"error": f"SD for '{resource_identifier}' not found, and core package download failed unexpectedly."}), 500
                else:
                    logger.info(f"Successfully downloaded core package {services.CANONICAL_PACKAGE_ID}.")
            except Exception as e:
                logger.error(f"Error downloading core package {services.CANONICAL_PACKAGE_ID}: {str(e)}", exc_info=True)
                return jsonify({"error": f"SD for '{resource_identifier}' not found, and error downloading core package: {str(e)}"}), 500
        if os.path.exists(core_tgz_path):
            try:
                sd_data, _ = services.find_and_extract_sd(core_tgz_path, resource_identifier)
                if sd_data is not None:
                    fallback_used = True
                    source_package_id = services.CANONICAL_PACKAGE_ID
                    logger.info(f"Found SD for '{resource_identifier}' in fallback package {source_package_id}.")
                else:
                    logger.error(f"SD for '{resource_identifier}' not found in primary package OR fallback {services.CANONICAL_PACKAGE_ID}.")
                    return jsonify({"error": f"StructureDefinition for '{resource_identifier}' not found in {package_name}#{package_version} or in core FHIR package."}), 404
            except Exception as e:
                logger.error(f"Error extracting SD for '{resource_identifier}' from fallback {core_tgz_path}: {e}", exc_info=True)
                return jsonify({"error": f"Error reading core FHIR package: {str(e)}"}), 500
        else:
            logger.error(f"Core package {core_tgz_path} missing even after download attempt.")
            return jsonify({"error": f"SD not found, and core package could not be located/downloaded."}), 500
    elements = sd_data.get('snapshot', {}).get('element', [])
    if not elements and 'differential' in sd_data:
        logger.debug(f"Using differential elements for {resource_identifier} as snapshot is missing.")
        elements = sd_data.get('differential', {}).get('element', [])
    if not elements:
        logger.warning(f"No snapshot or differential elements found in the SD for '{resource_identifier}' from {source_package_id}")
    must_support_paths = []
    processed_ig = ProcessedIg.query.filter_by(package_name=package_name, version=package_version).first()
    if processed_ig and processed_ig.must_support_elements:
        must_support_paths = processed_ig.must_support_elements.get(resource_identifier, [])
        logger.debug(f"Retrieved {len(must_support_paths)} Must Support paths for '{resource_identifier}' from processed IG {package_name}#{package_version}")
    response_data = {
        "elements": elements,
        "must_support_paths": must_support_paths,
        "fallback_used": fallback_used,
        "source_package": source_package_id,
        "requested_identifier": resource_identifier,
        "original_package": f"{package_name}#{package_version}"
    }
    return jsonify(response_data)

@app.route('/get-example')
def get_example_content():
    package_name = request.args.get('package_name')
    package_version = request.args.get('package_version')
    example_member_path = request.args.get('filename')
    if not all([package_name, package_version, example_member_path]):
        return jsonify({"error": "Missing required query parameters: package_name, package_version, filename"}), 400
    if not example_member_path.startswith('package/') or '..' in example_member_path:
        logger.warning(f"Invalid example file path requested: {example_member_path}")
        return jsonify({"error": "Invalid example file path."}), 400
    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir:
        logger.error("FHIR_PACKAGES_DIR not configured.")
        return jsonify({"error": "Server configuration error: Package directory not set."}), 500
    tgz_filename = services.construct_tgz_filename(package_name, package_version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    if not os.path.exists(tgz_path):
        logger.error(f"Package file not found for example extraction: {tgz_path}")
        return jsonify({"error": f"Package file not found: {package_name}#{package_version}"}), 404
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            try:
                example_member = tar.getmember(example_member_path)
                with tar.extractfile(example_member) as example_fileobj:
                    content_bytes = example_fileobj.read()
                content_string = content_bytes.decode('utf-8-sig')
                content_type = 'application/json' if example_member_path.lower().endswith('.json') else \
                               'application/xml' if example_member_path.lower().endswith('.xml') else \
                               'text/plain'
                return Response(content_string, mimetype=content_type)
            except KeyError:
                logger.error(f"Example file '{example_member_path}' not found within {tgz_filename}")
                return jsonify({"error": f"Example file '{os.path.basename(example_member_path)}' not found in package."}), 404
            except tarfile.TarError as e:
                logger.error(f"TarError reading example {example_member_path} from {tgz_filename}: {e}")
                return jsonify({"error": f"Error reading package archive: {e}"}), 500
            except UnicodeDecodeError as e:
                logger.error(f"Encoding error reading example {example_member_path} from {tgz_filename}: {e}")
                return jsonify({"error": f"Error decoding example file (invalid UTF-8?): {e}"}), 500
    except tarfile.TarError as e:
        logger.error(f"Error opening package file {tgz_path}: {e}")
        return jsonify({"error": f"Error reading package archive: {e}"}), 500
    except FileNotFoundError:
        logger.error(f"Package file disappeared: {tgz_path}")
        return jsonify({"error": f"Package file not found: {package_name}#{package_version}"}), 404
    except Exception as e:
        logger.error(f"Unexpected error getting example {example_member_path} from {tgz_filename}: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/get-package-metadata')
def get_package_metadata():
    package_name = request.args.get('package_name')
    version = request.args.get('version')
    if not package_name or not version:
        return jsonify({'error': 'Missing package_name or version parameter'}), 400
    try:
        metadata = services.get_package_metadata(package_name, version)
        if metadata:
            return jsonify({
                'package_name': metadata.get('package_name'),
                'version': metadata.get('version'),
                'dependency_mode': metadata.get('dependency_mode'),
                'imported_dependencies': metadata.get('imported_dependencies', []),
                'complies_with_profiles': metadata.get('complies_with_profiles', []),
                'imposed_profiles': metadata.get('imposed_profiles', [])
            })
        else:
            return jsonify({'error': 'Metadata file not found for this package version.'}), 404
    except Exception as e:
        logger.error(f"Error retrieving metadata for {package_name}#{version}: {e}", exc_info=True)
        return jsonify({'error': f'Error retrieving metadata: {str(e)}'}), 500

@app.route('/api/import-ig', methods=['POST'])
def api_import_ig():
    auth_error = check_api_key()
    if auth_error:
        return auth_error
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400
    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')
    dependency_mode = data.get('dependency_mode', 'recursive')
    if not package_name or not version:
        return jsonify({"status": "error", "message": "Missing package_name or version"}), 400
    if not (isinstance(package_name, str) and isinstance(version, str) and
            re.match(r'^[a-zA-Z0-9\-\.]+$', package_name) and
            re.match(r'^[a-zA-Z0-9\.\-\+]+$', version)):
        return jsonify({"status": "error", "message": "Invalid characters in package name or version"}), 400
    valid_modes = ['recursive', 'patch-canonical', 'tree-shaking', 'direct']
    if dependency_mode not in valid_modes:
        return jsonify({"status": "error", "message": f"Invalid dependency mode: {dependency_mode}. Must be one of {valid_modes}"}), 400
    try:
        result = services.import_package_and_dependencies(package_name, version, dependency_mode=dependency_mode)
        if result['errors'] and not result['downloaded']:
            error_msg = f"Failed to import {package_name}#{version}: {result['errors'][0]}"
            logger.error(f"[API] Import failed: {error_msg}")
            status_code = 404 if "404" in result['errors'][0] else 500
            return jsonify({"status": "error", "message": error_msg}), status_code
        package_filename = services.construct_tgz_filename(package_name, version)
        packages_dir = current_app.config.get('FHIR_PACKAGES_DIR', '/app/instance/fhir_packages')
        package_path = os.path.join(packages_dir, package_filename)
        complies_with_profiles = []
        imposed_profiles = []
        processing_errors = []
        if os.path.exists(package_path):
            logger.info(f"[API] Processing downloaded package {package_path} for metadata.")
            process_result = services.process_package_file(package_path)
            complies_with_profiles = process_result.get('complies_with_profiles', [])
            imposed_profiles = process_result.get('imposed_profiles', [])
            if process_result.get('errors'):
                processing_errors.extend(process_result['errors'])
                logger.warning(f"[API] Errors during post-import processing of {package_name}#{version}: {processing_errors}")
        else:
            logger.warning(f"[API] Package file {package_path} not found after reported successful download.")
            processing_errors.append("Package file disappeared after download.")
        all_packages, errors, duplicate_groups_after = list_downloaded_packages(packages_dir)
        duplicates_found = []
        for name, versions in duplicate_groups_after.items():
            duplicates_found.append(f"{name} (Versions present: {', '.join(versions)})")
        response_status = "success"
        response_message = "Package imported successfully."
        if result['errors'] or processing_errors:
            response_status = "warning"
            response_message = "Package imported, but some errors occurred during processing or dependency handling."
            all_issues = result.get('errors', []) + processing_errors
            logger.warning(f"[API] Import for {package_name}#{version} completed with warnings/errors: {all_issues}")
        response = {
            "status": response_status,
            "message": response_message,
            "package_name": package_name,
            "version": version,
            "dependency_mode": dependency_mode,
            "dependencies_processed": result.get('dependencies', []),
            "complies_with_profiles": complies_with_profiles,
            "imposed_profiles": imposed_profiles,
            "processing_issues": result.get('errors', []) + processing_errors,
            "duplicate_packages_present": duplicates_found
        }
        return jsonify(response), 200
    except Exception as e:
        logger.error(f"[API] Unexpected error in api_import_ig for {package_name}#{version}: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Unexpected server error during import: {str(e)}"}), 500

@app.route('/api/push-ig', methods=['POST'])
def api_push_ig():
    auth_error = check_api_key()
    if auth_error:
        return auth_error
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400
    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')
    fhir_server_url = data.get('fhir_server_url')
    include_dependencies = data.get('include_dependencies', True)
    if not all([package_name, version, fhir_server_url]):
        return jsonify({"status": "error", "message": "Missing package_name, version, or fhir_server_url"}), 400
    if not (isinstance(fhir_server_url, str) and fhir_server_url.startswith(('http://', 'https://'))):
        return jsonify({"status": "error", "message": "Invalid fhir_server_url format."}), 400
    if not (isinstance(package_name, str) and isinstance(version, str) and
            re.match(r'^[a-zA-Z0-9\-\.]+$', package_name) and
            re.match(r'^[a-zA-Z0-9\.\-\+]+$', version)):
        return jsonify({"status": "error", "message": "Invalid characters in package name or version"}), 400
    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir:
        logger.error("[API Push] FHIR_PACKAGES_DIR not configured.")
        return jsonify({"status": "error", "message": "Server configuration error: Package directory not set."}), 500
    tgz_filename = services.construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    if not os.path.exists(tgz_path):
        logger.error(f"[API Push] Main package not found: {tgz_path}")
        return jsonify({"status": "error", "message": f"Package not found locally: {package_name}#{version}"}), 404
    def generate_stream():
        pushed_packages_info = []
        success_count = 0
        failure_count = 0
        validation_failure_count = 0
        total_resources_attempted = 0
        processed_resources = set()
        try:
            yield json.dumps({"type": "start", "message": f"Starting push for {package_name}#{version} to {fhir_server_url}"}) + "\n"
            packages_to_push = [(package_name, version, tgz_path)]
            dependencies_to_include = []
            if include_dependencies:
                yield json.dumps({"type": "progress", "message": "Checking dependencies..."}) + "\n"
                metadata = services.get_package_metadata(package_name, version)
                if metadata and metadata.get('imported_dependencies'):
                    dependencies_to_include = metadata['imported_dependencies']
                    yield json.dumps({"type": "info", "message": f"Found {len(dependencies_to_include)} dependencies in metadata."}) + "\n"
                    for dep in dependencies_to_include:
                        dep_name = dep.get('name')
                        dep_version = dep.get('version')
                        if not dep_name or not dep_version: continue
                        dep_tgz_filename = services.construct_tgz_filename(dep_name, dep_version)
                        dep_tgz_path = os.path.join(packages_dir, dep_tgz_filename)
                        if os.path.exists(dep_tgz_path):
                            packages_to_push.append((dep_name, dep_version, dep_tgz_path))
                            yield json.dumps({"type": "progress", "message": f"Queued dependency: {dep_name}#{dep_version}"}) + "\n"
                        else:
                            yield json.dumps({"type": "warning", "message": f"Dependency package file not found, skipping: {dep_name}#{dep_version} ({dep_tgz_filename})"}) + "\n"
                else:
                    yield json.dumps({"type": "info", "message": "No dependency metadata found or no dependencies listed."}) + "\n"
            resources_to_upload = []
            seen_resource_files = set()
            for pkg_name, pkg_version, pkg_path in packages_to_push:
                yield json.dumps({"type": "progress", "message": f"Extracting resources from {pkg_name}#{pkg_version}..."}) + "\n"
                try:
                    with tarfile.open(pkg_path, "r:gz") as tar:
                        for member in tar.getmembers():
                            if (member.isfile() and
                                member.name.startswith('package/') and
                                member.name.lower().endswith('.json') and
                                os.path.basename(member.name).lower() not in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']):
                                if member.name in seen_resource_files:
                                    continue
                                seen_resource_files.add(member.name)
                                try:
                                    with tar.extractfile(member) as f:
                                        resource_data = json.load(f)
                                        if isinstance(resource_data, dict) and 'resourceType' in resource_data and 'id' in resource_data:
                                            resources_to_upload.append({
                                                "data": resource_data,
                                                "source_package": f"{pkg_name}#{pkg_version}",
                                                "source_filename": member.name
                                            })
                                        else:
                                            yield json.dumps({"type": "warning", "message": f"Skipping invalid/incomplete resource in {member.name} from {pkg_name}#{pkg_version}"}) + "\n"
                                except (json.JSONDecodeError, UnicodeDecodeError) as json_e:
                                    yield json.dumps({"type": "warning", "message": f"Skipping non-JSON or corrupt file {member.name} from {pkg_name}#{pkg_version}: {json_e}"}) + "\n"
                                except Exception as extract_e:
                                    yield json.dumps({"type": "warning", "message": f"Error extracting file {member.name} from {pkg_name}#{pkg_version}: {extract_e}"}) + "\n"
                except (tarfile.TarError, FileNotFoundError) as tar_e:
                    yield json.dumps({"type": "error", "message": f"Error reading package {pkg_name}#{pkg_version}: {tar_e}. Skipping its resources."}) + "\n"
                    failure_count += 1
                    continue
            total_resources_attempted = len(resources_to_upload)
            yield json.dumps({"type": "info", "message": f"Found {total_resources_attempted} potential resources to upload."}) + "\n"
            session = requests.Session()
            headers = {'Content-Type': 'application/fhir+json', 'Accept': 'application/fhir+json'}
            for i, resource_info in enumerate(resources_to_upload, 1):
                resource = resource_info["data"]
                source_pkg = resource_info["source_package"]
                source_file = resource_info["source_filename"]
                resource_type = resource.get('resourceType')
                resource_id = resource.get('id')
                resource_log_id = f"{resource_type}/{resource_id}"
                if resource_log_id in processed_resources:
                    yield json.dumps({"type": "info", "message": f"Skipping duplicate resource: {resource_log_id} (already uploaded or queued)"}) + "\n"
                    continue
                processed_resources.add(resource_log_id)
                resource_url = f"{fhir_server_url.rstrip('/')}/{resource_type}/{resource_id}"
                yield json.dumps({"type": "progress", "message": f"Uploading {resource_log_id} ({i}/{total_resources_attempted}) from {source_pkg}..."}) + "\n"
                try:
                    response = session.put(resource_url, json=resource, headers=headers, timeout=30)
                    response.raise_for_status()
                    yield json.dumps({"type": "success", "message": f"Uploaded {resource_log_id} successfully (Status: {response.status_code})"}) + "\n"
                    success_count += 1
                    if source_pkg not in [p["id"] for p in pushed_packages_info]:
                        pushed_packages_info.append({"id": source_pkg, "resource_count": 1})
                    else:
                        for p in pushed_packages_info:
                            if p["id"] == source_pkg:
                                p["resource_count"] += 1
                                break
                except requests.exceptions.Timeout:
                    error_msg = f"Timeout uploading {resource_log_id} to {resource_url}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                except requests.exceptions.ConnectionError as e:
                    error_msg = f"Connection error uploading {resource_log_id}: {e}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                except requests.exceptions.HTTPError as e:
                    outcome_text = ""
                    try:
                        outcome = e.response.json()
                        if outcome and outcome.get('resourceType') == 'OperationOutcome' and outcome.get('issue'):
                            outcome_text = "; ".join([f"{issue.get('severity')}: {issue.get('diagnostics')}" for issue in outcome['issue']])
                    except:
                        outcome_text = e.response.text[:200]
                    error_msg = f"Failed to upload {resource_log_id} (Status: {e.response.status_code}): {outcome_text or str(e)}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                except requests.exceptions.RequestException as e:
                    error_msg = f"Failed to upload {resource_log_id}: {str(e)}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                except Exception as e:
                    error_msg = f"Unexpected error uploading {resource_log_id}: {str(e)}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                    logger.error(f"[API Push] Unexpected upload error: {e}", exc_info=True)
            final_status = "success" if failure_count == 0 and total_resources_attempted > 0 else \
                           "partial" if success_count > 0 else \
                           "failure"
            summary_message = f"Push finished: {success_count} succeeded, {failure_count} failed out of {total_resources_attempted} resources attempted."
            if validation_failure_count > 0:
                summary_message += f" ({validation_failure_count} failed validation)."
            summary = {
                "status": final_status,
                "message": summary_message,
                "target_server": fhir_server_url,
                "package_name": package_name,
                "version": version,
                "included_dependencies": include_dependencies,
                "resources_attempted": total_resources_attempted,
                "success_count": success_count,
                "failure_count": failure_count,
                "validation_failure_count": validation_failure_count,
                "pushed_packages_summary": pushed_packages_info
            }
            yield json.dumps({"type": "complete", "data": summary}) + "\n"
            logger.info(f"[API Push] Completed for {package_name}#{version}. Status: {final_status}. {summary_message}")
        except Exception as e:
            logger.error(f"[API Push] Critical error during push stream generation: {str(e)}", exc_info=True)
            error_response = {
                "status": "error",
                "message": f"Server error during push operation: {str(e)}"
            }
            try:
                yield json.dumps({"type": "error", "message": error_response["message"]}) + "\n"
                yield json.dumps({"type": "complete", "data": error_response}) + "\n"
            except GeneratorExit:
                logger.warning("[API Push] Stream closed before final error could be sent.")
            except Exception as yield_e:
                logger.error(f"[API Push] Error yielding final error message: {yield_e}")
    return Response(generate_stream(), mimetype='application/x-ndjson')

@app.route('/validate-sample', methods=['GET', 'POST'])
def validate_sample():
    form = ValidationForm()
    validation_report = None
    packages_for_template = []
    packages_dir = app.config.get('FHIR_PACKAGES_DIR')
    if packages_dir:
        try:
            all_packages, errors, duplicate_groups = list_downloaded_packages(packages_dir)
            if errors:
                flash(f"Warning: Errors encountered while listing packages: {', '.join(errors)}", "warning")
            filtered_packages = [
                pkg for pkg in all_packages
                if isinstance(pkg.get('name'), str) and pkg.get('name') and
                   isinstance(pkg.get('version'), str) and pkg.get('version')
            ]
            packages_for_template = sorted([
                {"id": f"{pkg['name']}#{pkg['version']}", "text": f"{pkg['name']}#{pkg['version']}"}
                for pkg in filtered_packages
            ], key=lambda x: x['text'])
            logger.debug(f"Packages for template: {packages_for_template}")
        except Exception as e:
            logger.error(f"Failed to list or process downloaded packages: {e}", exc_info=True)
            flash("Error loading available packages.", "danger")
    else:
        flash("FHIR Packages directory not configured.", "danger")
        logger.error("FHIR_PACKAGES_DIR is not configured in the Flask app.")

    if form.validate_on_submit():
        package_name = form.package_name.data
        version = form.version.data
        include_dependencies = form.include_dependencies.data
        mode = form.mode.data
        sample_input_raw = form.sample_input.data

        try:
            sample_input = json.loads(sample_input_raw)
            logger.info(f"Starting validation (mode: {mode}) for {package_name}#{version}, deps: {include_dependencies}")
            if mode == 'single':
                validation_report = services.validate_resource_against_profile(
                    package_name, version, sample_input, include_dependencies
                )
            elif mode == 'bundle':
                validation_report = services.validate_bundle_against_profile(
                    package_name, version, sample_input, include_dependencies
                )
            else:
                flash("Invalid validation mode selected.", "error")
                validation_report = None
            if validation_report:
                flash("Validation completed.", 'info')
                logger.info(f"Validation Result: Valid={validation_report.get('valid')}, Errors={len(validation_report.get('errors',[]))}, Warnings={len(validation_report.get('warnings',[]))}")
        except json.JSONDecodeError:
            flash("Invalid JSON format in sample input.", 'error')
            logger.warning("Validation failed: Invalid JSON input.")
            validation_report = {'valid': False, 'errors': ['Invalid JSON format provided.'], 'warnings': [], 'results': {}}
        except FileNotFoundError as e:
            flash(f"Validation Error: Required package file not found for {package_name}#{version}. Please ensure it's downloaded.", 'error')
            logger.error(f"Validation failed: Package file missing - {e}")
            validation_report = {'valid': False, 'errors': [f"Required package file not found: {package_name}#{version}"], 'warnings': [], 'results': {}}
        except Exception as e:
            logger.error(f"Error validating sample: {e}", exc_info=True)
            flash(f"An unexpected error occurred during validation: {str(e)}", 'error')
            validation_report = {'valid': False, 'errors': [f'Unexpected error: {str(e)}'], 'warnings': [], 'results': {}}
    else:
        for field, errors in form.errors.items():
            field_obj = getattr(form, field, None)
            field_label = field_obj.label.text if field_obj and hasattr(field_obj, 'label') else field
            for error in errors:
                flash(f"Error in field '{field_label}': {error}", "danger")

    return render_template(
        'validate_sample.html',
        form=form,
        packages=packages_for_template,
        validation_report=validation_report,
        site_name='FHIRFLARE IG Toolkit',
        now=datetime.datetime.now()
    )

# --- App Initialization ---
def create_db():
    logger.debug(f"Attempting to create database tables for URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
    try:
        db.create_all()
        logger.info("Database tables created successfully (if they didn't exist).")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}", exc_info=True)
        raise

with app.app_context():
    create_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)