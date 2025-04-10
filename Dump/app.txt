from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
import os
import services  # Assuming your existing services module for FHIR IG handling

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/fhir_ig.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FHIR_PACKAGES_DIR'] = os.path.join(app.instance_path, 'fhir_packages')
os.makedirs(app.config['FHIR_PACKAGES_DIR'], exist_ok=True)

db = SQLAlchemy(app)

# Simplified ProcessedIg model (no user-related fields)
class ProcessedIg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    package_name = db.Column(db.String(128), nullable=False)
    version = db.Column(db.String(32), nullable=False)
    processed_date = db.Column(db.DateTime, nullable=False)
    resource_types_info = db.Column(db.JSON, nullable=False)  # List of resource type metadata
    must_support_elements = db.Column(db.JSON, nullable=True)  # Dict of MS elements
    examples = db.Column(db.JSON, nullable=True)  # Dict of example filepaths

# Landing page with two buttons
@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FHIR IG Toolkit</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        .button { padding: 15px 30px; margin: 10px; font-size: 16px; }
    </style>
</head>
<body>
    <h1>FHIR IG Toolkit</h1>
    <p>Simple tool for importing and viewing FHIR Implementation Guides.</p>
    <a href="{{ url_for('import_ig') }}"><button class="button">Import FHIR IG</button></a>
    <a href="{{ url_for('view_igs') }}"><button class="button">View Downloaded IGs</button></a>
</body>
</html>
    ''')

# Import IG route
@app.route('/import-ig', methods=['GET', 'POST'])
def import_ig():
    if request.method == 'POST':
        name = request.form.get('name')
        version = request.form.get('version', 'latest')
        try:
            # Call your existing service to download package and dependencies
            result = services.import_package_and_dependencies(name, version, app.config['FHIR_PACKAGES_DIR'])
            downloaded_files = result.get('downloaded', [])
            for file_path in downloaded_files:
                # Process each downloaded package
                package_info = services.process_package_file(file_path)
                processed_ig = ProcessedIg(
                    package_name=package_info['name'],
                    version=package_info['version'],
                    processed_date=package_info['processed_date'],
                    resource_types_info=package_info['resource_types_info'],
                    must_support_elements=package_info.get('must_support_elements'),
                    examples=package_info.get('examples')
                )
                db.session.add(processed_ig)
            db.session.commit()
            flash(f"Successfully imported {name} {version} and dependencies!", "success")
            return redirect(url_for('view_igs'))
        except Exception as e:
            flash(f"Error importing IG: {str(e)}", "error")
            return redirect(url_for('import_ig'))
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Import FHIR IG</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; }
        .form { max-width: 400px; margin: 0 auto; }
        .field { margin: 10px 0; }
        input[type="text"] { width: 100%; padding: 5px; }
        .button { padding: 10px 20px; }
        .message { color: {% if category == "success" %}green{% else %}red{% endif %}; }
    </style>
</head>
<body>
    <h1>Import FHIR IG</h1>
    <form class="form" method="POST">
        <div class="field">
            <label>Package Name:</label>
            <input type="text" name="name" placeholder="e.g., hl7.fhir.us.core" required>
        </div>
        <div class="field">
            <label>Version (optional):</label>
            <input type="text" name="version" placeholder="e.g., 1.0.0 or latest">
        </div>
        <button class="button" type="submit">Import</button>
        <a href="{{ url_for('index') }}"><button class="button" type="button">Back</button></a>
    </form>
    {% with messages = get_flashed_messages(with_categories=True) %}
        {% if messages %}
            {% for category, message in messages %}
                <p class="message">{{ message }}</p>
            {% endfor %}
        {% endif %}
    {% endwith %}
</body>
</html>
    ''')

# View Downloaded IGs route
@app.route('/view-igs')
def view_igs():
    igs = ProcessedIg.query.all()
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>View Downloaded IGs</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; }
        table { width: 80%; margin: 20px auto; border-collapse: collapse; }
        th, td { padding: 10px; border: 1px solid #ddd; text-align: left; }
        th { background-color: #f2f2f2; }
        .button { padding: 10px 20px; }
    </style>
</head>
<body>
    <h1>Downloaded FHIR IGs</h1>
    <table>
        <tr>
            <th>Package Name</th>
            <th>Version</th>
            <th>Processed Date</th>
            <th>Resource Types</th>
        </tr>
        {% for ig in igs %}
        <tr>
            <td>{{ ig.package_name }}</td>
            <td>{{ ig.version }}</td>
            <td>{{ ig.processed_date }}</td>
            <td>{{ ig.resource_types_info | length }} types</td>
        </tr>
        {% endfor %}
    </table>
    <a href="{{ url_for('index') }}"><button class="button">Back</button></a>
</body>
</html>
    ''', igs=igs)

# Initialize DB
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)