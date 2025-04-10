from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, jsonify
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

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/instance/fhir_ig.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FHIR_PACKAGES_DIR'] = os.path.join(app.instance_path, 'fhir_packages')

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
        Regexp(r'^[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)+$', message='Invalid package name format.')
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

@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FHIR IG Toolkit</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { text-align: center; padding: 50px; }
        .button { padding: 15px 30px; margin: 10px; font-size: 16px; }
    </style>
</head>
<body>
    <h1>FHIR IG Toolkit</h1>
    <p>Simple tool for importing and viewing FHIR Implementation Guides.</p>
    <a href="{{ url_for('import_ig') }}"><button class="button btn btn-primary">Import FHIR IG</button></a>
    <a href="{{ url_for('view_igs') }}"><button class="button btn btn-primary">View Downloaded IGs</button></a>
</body>
</html>
    ''')

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
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Import FHIR IG</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .form { max-width: 400px; margin: 20px auto; }
        .message { margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mt-4">Import FHIR IG</h1>
        <form class="form" method="POST">
            {{ form.hidden_tag() }}
            <div class="mb-3">
                {{ form.package_name.label(class="form-label") }}
                {{ form.package_name(class="form-control") }}
                {% for error in form.package_name.errors %}<p class="text-danger message">{{ error }}</p>{% endfor %}
            </div>
            <div class="mb-3">
                {{ form.package_version.label(class="form-label") }}
                {{ form.package_version(class="form-control") }}
                {% for error in form.package_version.errors %}<p class="text-danger message">{{ error }}</p>{% endfor %}
            </div>
            {{ form.submit(class="btn btn-primary") }}
            <a href="{{ url_for('index') }}" class="btn btn-secondary">Back</a>
        </form>
        {% with messages = get_flashed_messages(with_categories=True) %}
            {% if messages %}
                {% for category, message in messages %}
                    <p class="message text-{{ 'success' if category == 'success' else 'danger' }}">{{ message }}</p>
                {% endfor %}
            {% endif %}
        {% endwith %}
    </div>
</body>
</html>
    ''', form=form)

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
                name_version = filename[:-4]  # Remove .tgz
                parts = name_version.split('-')
                version_start = -1
                for i, part in enumerate(parts):
                    if part[0].isdigit() or part in ('preview', 'current', 'latest'):
                        version_start = i
                        break
                if version_start > 0:  # Ensure there's a name before version
                    name = '.'.join(parts[:version_start])
                    version = '-'.join(parts[version_start:])
                    packages.append({'name': name, 'version': version, 'filename': filename})
                else:
                    # Fallback: treat as name only, log warning
                    name = name_version
                    version = ''
                    logger.warning(f"Could not parse version from {filename}, treating as name only")
                    packages.append({'name': name, 'version': version, 'filename': filename})
        logger.debug(f"Found packages: {packages}")
    else:
        logger.warning(f"Packages directory not found: {packages_dir}")

    duplicate_names = {}
    duplicate_groups = {}
    for pkg in packages:
        name = pkg['name']
        if name in duplicate_names:
            duplicate_names[name].append(pkg)
            duplicate_groups.setdefault(name, []).append(pkg['version'])
        else:
            duplicate_names[name] = [pkg]

    # Precompute group colors
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger']
    group_colors = {}
    for i, name in enumerate(duplicate_groups):
        if len(duplicate_groups[name]) > 1:  # Only color duplicates
            group_colors[name] = colors[i % len(colors)]

    return render_template('cp_downloaded_igs.html', packages=packages, processed_list=igs, 
                         processed_ids=processed_ids, duplicate_names=duplicate_names, 
                         duplicate_groups=duplicate_groups, group_colors=group_colors)

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
        name_version = filename[:-4]
        parts = name_version.split('-')
        version_start = -1
        for i, part in enumerate(parts):
            if part[0].isdigit() or part in ('preview', 'current', 'latest'):
                version_start = i
                break
        if version_start > 0:
            name = '.'.join(parts[:version_start])
            version = '-'.join(parts[version_start:])
        else:
            name = name_version
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
                          processed_ig=processed_ig, profile_list=profile_list, base_list=base_list, examples_by_type=examples_by_type)

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

with app.app_context():
    logger.debug(f"Creating database at: {app.config['SQLALCHEMY_DATABASE_URI']}")
    db.create_all()
    logger.debug("Database initialization complete")

if __name__ == '__main__':
    app.run(debug=True)