FHIRFLARE IG Toolkit
Overview
The FHIRFLARE IG Toolkit is a Flask-based web application designed to streamline the management, processing, validation, and deployment of FHIR Implementation Guides (IGs). It offers a user-friendly interface for importing IG packages, extracting metadata, validating FHIR resources or bundles, pushing IGs to FHIR servers, and converting FHIR resources to FHIR Shorthand (FSH) using GoFSH with advanced features. The toolkit includes a live console for real-time feedback and a waiting spinner for FSH conversion, making it an essential tool for FHIR developers and implementers.
The application runs in a Dockerized environment with a Flask frontend, SQLite database, and an embedded HAPI FHIR server, ensuring consistent deployment and easy setup across platforms.
Features

Import IGs: Download FHIR IG packages and dependencies from a package registry, supporting flexible version formats (e.g., 1.2.3, 1.1.0-preview, 1.1.2-ballot, current).
Manage IGs: View, process, unload, or delete downloaded IGs, with duplicate detection and resolution.
Process IGs: Extract resource types, profiles, must-support elements, examples, and profile relationships (structuredefinition-compliesWithProfile and structuredefinition-imposeProfile).
Validate FHIR Resources/Bundles: Validate single FHIR resources or bundles against selected IGs, with detailed error and warning reports (alpha feature, work in progress).
Push IGs: Upload IG resources to a FHIR server (e.g., HAPI FHIR) with real-time console output and optional validation against imposed profiles.
Profile Relationships: Display and validate compliesWithProfile and imposeProfile extensions in the UI.
FSH Converter: Convert FHIR JSON/XML resources to FHIR Shorthand (FSH) using GoFSH, with advanced options:
Package selection for context (e.g., hl7.fhir.au.core#1.1.0-preview).
Input modes: File upload or text input.
Output styles: file-per-definition, group-by-fsh-type, group-by-profile, single-file.
Log levels: error, warn, info, debug.
FHIR versions: R4, R4B, R5, or auto-detect.
Fishing Trip: Round-trip validation with SUSHI, generating a comparison report (fshing-trip-comparison.html) accessible via a "Click here for SUSHI Validation" badge button.
Dependencies: Load additional FHIR packages (e.g., hl7.fhir.us.core@6.1.0).
Indented Rules: Output FSH with context path indentation for readability.
Meta Profile Handling: Control meta.profile usage (only-one, first, none).
Alias File: Load existing FSH aliases (e.g., $MyAlias = http://example.org).
No Alias: Disable automatic alias generation.
Waiting Spinner: Displays a themed animation (light/dark) during FSH execution to indicate processing.


API Support: RESTful API endpoints for importing, pushing, and retrieving IG metadata, including profile relationships.
Live Console: Real-time logs for push, validation, and FSH conversion operations.
Configurable Behavior: Enable/disable imposed profile validation and UI display of profile relationships.

Technology Stack

Python 3.12+: Core backend language.
Flask 2.3.3: Web framework for the application.
Flask-SQLAlchemy 3.0.5: ORM for SQLite database management.
Flask-WTF 1.2.1: Form creation, validation, and CSRF protection.
Jinja2: Templating engine for HTML rendering.
Bootstrap 5.3.3: Responsive frontend framework.
JavaScript (ES6): Client-side scripting for interactive features (e.g., live console, form toggles, waiting spinner).
Lottie-Web 5.12.2: Renders JSON-based animations for the FSH converter waiting spinner.
SQLite: Lightweight database for processed IG metadata.
Docker: Containerization with Flask and HAPI FHIR server.
Node.js 18+: For GoFSH and SUSHI, used in the FSH Converter feature.
GoFSH: Tool for converting FHIR resources to FHIR Shorthand (FSH).
SUSHI: FSH compiler for round-trip validation in Fishing Trip.
Requests 2.31.0: HTTP requests to FHIR servers.
Tarfile: Handling .tgz package files.
Logging: Python’s built-in logging for debugging.

Prerequisites

Docker: Required for containerized deployment.
Git: For cloning repositories.
Maven: For building the HAPI FHIR server.
Windows (if using batch files): For running Build and Run for first time.bat and Run.bat.
Linux/MacOS (if using manual steps): For running equivalent commands.

Setup Instructions
The toolkit can be set up using batch files (Windows) or manual steps (cross-platform).
Using Batch Files (Windows)
First-Time Setup and Build:

Run Build and Run for first time.bat:
cd "<project folder>"
git clone https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git hapi-fhir-jpaserver
copy .\hapi-fhir-Setup\target\classes\application.yaml .\hapi-fhir-jpaserver\target\classes\application.yaml
mvn clean package -DskipTests=true -Pboot
docker-compose build --no-cache
docker-compose up -d

This clones the HAPI FHIR server, copies configuration, builds the project, and starts the containers.


Subsequent Runs:

Run Run.bat:
cd "<project folder>"
docker-compose up -d

This starts the Flask app (port 5000) and HAPI FHIR server (port 8080).


Access the Application:

Flask UI: http://localhost:5000
HAPI FHIR server: http://localhost:8080

Manual Setup (Linux/MacOS/Windows)
Preparation:
cd <project folder>
git clone https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git hapi-fhir-jpaserver
cp ./hapi-fhir-Setup/target/classes/application.yaml ./hapi-fhir-jpaserver/target/classes/application.yaml

Build:
mvn clean package -DskipTests=true -Pboot
docker-compose build --no-cache

Run:
docker-compose up -d

Access the Application:

Flask UI: http://localhost:5000
HAPI FHIR server: http://localhost:8080

Local Development (Without Docker)
Clone the Repository:
git clone https://github.com/Sudo-JHare/FHIRFLARE-IG-Toolkit.git
cd FHIRFLARE-IG-Toolkit

Install Dependencies:
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

Install Node.js, GoFSH, and SUSHI (for FSH Converter):
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo bash -
sudo apt-get install -y nodejs
npm install -g gofsh fsh-sushi

Set Environment Variables:
export FLASK_SECRET_KEY='your-secure-secret-key'
export API_KEY='your-api-key'

Initialize Directories:
mkdir -p instance static/uploads logs
chmod -R 777 instance static/uploads logs

Run the Application:
export FLASK_APP=app.py
flask run

Access at http://localhost:5000.
Usage
Import an IG

Navigate to Import IG (/import-ig).
Enter a package name (e.g., hl7.fhir.au.core) and version (e.g., 1.1.0-preview).
Choose a dependency mode:
Recursive: Import all dependencies.
Patch Canonical: Import only canonical FHIR packages.
Tree Shaking: Import only used dependencies.


Click Import to download the package and dependencies.

Manage IGs

Go to Manage FHIR Packages (/view-igs) to view downloaded and processed IGs.
Actions:
Process: Extract metadata (resource types, profiles, must-support elements, examples).
Unload: Remove processed IG data from the database.
Delete: Remove package files from the filesystem.


Duplicates are highlighted for resolution.

View Processed IGs

After processing, view IG details (/view-ig/<id>), including:
Resource types and profiles.
Must-support elements and examples.
Profile relationships (compliesWithProfile, imposeProfile) if enabled (DISPLAY_PROFILE_RELATIONSHIPS).



Validate FHIR Resources/Bundles

Navigate to Validate FHIR Sample (/validate-sample).
Select a package (e.g., hl7.fhir.au.core#1.1.0-preview).
Choose Single Resource or Bundle mode.
Paste or upload FHIR JSON/XML (e.g., a Patient resource).
Submit to view validation errors/warnings.
Note: Alpha feature; report issues to GitHub (remove PHI).

Push IGs to a FHIR Server

Go to Push IGs (/push-igs).
Select a package, enter a FHIR server URL (e.g., http://localhost:8080/fhir), and choose whether to include dependencies.
Click Push to FHIR Server to upload resources, with validation against imposed profiles (if enabled via VALIDATE_IMPOSED_PROFILES).
Monitor progress in the live console.

Convert FHIR to FSH

Navigate to FSH Converter (/fsh-converter).
Optionally select a package for context (e.g., hl7.fhir.au.core#1.1.0-preview).
Choose input mode:
Upload File: Upload a FHIR JSON/XML file.
Paste Text: Paste FHIR JSON/XML content.


Configure options:
Output Style: file-per-definition, group-by-fsh-type, group-by-profile, single-file.
Log Level: error, warn, info, debug.
FHIR Version: R4, R4B, R5, or auto-detect.
Fishing Trip: Enable round-trip validation with SUSHI, generating a comparison report.
Dependencies: Specify additional packages (e.g., hl7.fhir.us.core@6.1.0, one per line).
Indent Rules: Enable context path indentation for readable FSH.
Meta Profile: Choose only-one, first, or none for meta.profile handling.
Alias File: Upload an FSH file with aliases (e.g., $MyAlias = http://example.org).
No Alias: Disable automatic alias generation.


Click Convert to FSH to generate and display FSH output, with a waiting spinner (light/dark theme) during processing.
If Fishing Trip is enabled, view the comparison report via the "Click here for SUSHI Validation" badge button.
Download the result as a .fsh file.

Example Input:
{
    "resourceType": "Patient",
    "id": "banks-mia-leanne",
    "meta": {
        "profile": ["http://hl7.org.au/fhir/core/StructureDefinition/au-core-patient"]
    },
    "name": [
        {
            "family": "Banks",
            "given": ["Mia", "Leanne"]
        }
    ]
}

Example Output:
Profile: AUCorePatient
Parent: Patient
* name 1..*
* name.family 1..1
* name.given 1..*

Explore FHIR Operations

Navigate to FHIR UI Operations (/fhir-ui-operations).
Toggle between local HAPI (/fhir) or a custom FHIR server.
Click Fetch Metadata to load the server’s CapabilityStatement.
Select a resource type (e.g., Patient, Observation) or System to view operations:
System operations: GET /metadata, POST /, GET /_history, GET/POST /$diff, POST /$reindex, POST /$expunge, etc.
Resource operations: GET Patient/:id, POST Observation/_search, etc.


Use Try it out to input parameters or request bodies, then Execute to view results in JSON, XML, or narrative formats.

API Usage
Import IG
curl -X POST http://localhost:5000/api/import-ig \
-H "Content-Type: application/json" \
-d '{"package_name": "hl7.fhir.au.core", "version": "1.1.0-preview", "api_key": "your-api-key"}'

Returns complies_with_profiles, imposed_profiles, and duplicate info.
Push IG
curl -X POST http://localhost:5000/api/push-ig \
-H "Content-Type: application/json" \
-H "Accept: application/x-ndjson" \
-d '{"package_name": "hl7.fhir.au.core", "version": "1.1.0-preview", "fhir_server_url": "http://localhost:8080/fhir", "include_dependencies": true, "api_key": "your-api-key"}'

Validates resources against imposed profiles (if enabled).
Validate Resource/Bundle
Not yet exposed via API; use the UI at /validate-sample.
Configuration Options

VALIDATE_IMPOSED_PROFILES:

Default: True

Validates resources against imposed profiles during push.

Set to False to skip:
app.config['VALIDATE_IMPOSED_PROFILES'] = False




DISPLAY_PROFILE_RELATIONSHIPS:

Default: True

Shows compliesWithProfile and imposeProfile in the UI.

Set to False to hide:
app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = False




FHIR_PACKAGES_DIR:

Default: /app/instance/fhir_packages
Stores .tgz packages and metadata.


UPLOAD_FOLDER:

Default: /app/static/uploads
Stores GoFSH output files and FSH comparison reports.


SECRET_KEY:

Required for CSRF protection and sessions:
app.config['SECRET_KEY'] = 'your-secure-secret-key'




API_KEY:

Required for API authentication:
app.config['API_KEY'] = 'your-api-key'





Testing
The project includes a test suite covering UI, API, database, file operations, and security.
Test Prerequisites

pytest: For running tests.
pytest-mock: For mocking dependencies.

Install:
pip install pytest pytest-mock

Running Tests
cd <project folder>
pytest tests/test_app.py -v

Test Coverage

UI Pages: Homepage, Import IG, Manage IGs, Push IGs, Validate Sample, View Processed IG, FSH Converter.
API Endpoints: POST /api/import-ig, POST /api/push-ig, GET /get-structure, GET /get-example.
Database: IG processing, unloading, viewing.
File Operations: Package processing, deletion, FSH output.
Security: CSRF protection, flash messages, secret key.
FSH Converter: Form submission, file/text input, GoFSH execution, Fishing Trip comparison.

Example Test Output
================================================================ test session starts =================================================================
platform linux -- Python 3.12, pytest-8.3.5, pluggy-1.5.0
rootdir: /app/tests
collected 27 items

test_app.py::TestFHIRFlareIGToolkit::test_homepage PASSED                         [  3%]
test_app.py::TestFHIRFlareIGToolkit::test_import_ig_page PASSED                   [  7%]
test_app.py::TestFHIRFlareIGToolkit::test_fsh_converter_page PASSED               [ 11%]
...
test_app.py::TestFHIRFlareIGToolkit::test_validate_sample_success PASSED          [ 88%]
============================================================= 27 passed in 1.23s ==============================================================

Troubleshooting Tests

ModuleNotFoundError: Ensure app.py, services.py, forms.py are in /app/.
TemplateNotFound: Verify templates are in /app/templates/.
Database Errors: Ensure instance/fhir_ig.db is writable (chmod 777 instance).
Mock Failures: Check tests/test_app.py for correct mocking.

Development Notes
Background
The toolkit addresses the need for a comprehensive FHIR IG management tool, with recent enhancements for resource validation, FSH conversion with advanced GoFSH features, and flexible versioning, making it a versatile platform for FHIR developers.
Technical Decisions

Flask: Lightweight and flexible for web development.
SQLite: Simple for development; consider PostgreSQL for production.
Bootstrap 5.3.3: Responsive UI with custom styling for duplicates, FSH output, and waiting spinner.
Lottie-Web: Renders themed animations for FSH conversion waiting spinner.
GoFSH/SUSHI: Integrated via Node.js for advanced FSH conversion and round-trip validation.
Docker: Ensures consistent deployment with Flask and HAPI FHIR.
Flexible Versioning: Supports non-standard IG versions (e.g., -preview, -ballot).
Live Console: Real-time feedback for complex operations.
Validation: Alpha feature with ongoing FHIRPath improvements.

Recent Updates

Waiting Spinner for FSH Converter (April 2025):
Added a themed (light/dark) Lottie animation spinner during FSH execution to indicate processing.
Path: templates/fsh_converter.html, static/animations/loading-dark.json, static/animations/loading-light.json, static/js/lottie-web.min.js.


Advanced FSH Converter (April 2025):
Added support for GoFSH advanced options: --fshing-trip (round-trip validation with SUSHI), --dependency (additional packages), --indent (indented rules), --meta-profile (only-one, first, none), --alias-file (custom aliases), --no-alias (disable alias generation).
Displays Fishing Trip comparison reports via a badge button.
Path: templates/fsh_converter.html, app.py, services.py, forms.py.


FSH Converter (April 2025):
Added /fsh-converter page for FHIR to FSH conversion using GoFSH.
Path: templates/fsh_converter.html, app.py, services.py, forms.py.


Favicon Fix (April 2025):
Resolved 404 for /favicon.ico on /fsh-converter by ensuring static/favicon.ico is served.
Added fallback /favicon.ico route in app.py.


Menu Item (April 2025):
Added “FSH Converter” to the navbar in base.html.


UPLOAD_FOLDER Fix (April 2025):
Fixed 500 error on /fsh-converter by setting app.config['UPLOAD_FOLDER'] = '/app/static/uploads'.


Validation (April 2025):
Alpha support for validating resources/bundles in /validate-sample.
Path: templates/validate_sample.html, app.py, services.py.


CSRF Protection: Fixed missing CSRF tokens in cp_downloaded_igs.html, cp_push_igs.html.
Version Support: Added flexible version formats (e.g., 1.1.0-preview) in forms.py.

Known Issues and Workarounds

Favicon 404: Clear browser cache or verify /app/static/favicon.ico:
docker exec -it <container_name> curl http://localhost:5000/static/favicon.ico


CSRF Errors: Set FLASK_SECRET_KEY and ensure {{ form.hidden_tag() }} in forms.

Import Fails: Check package name/version and connectivity.

Validation Accuracy: Alpha feature; FHIRPath may miss complex constraints. Report issues to GitHub (remove PHI).

Package Parsing: Non-standard .tgz filenames may parse incorrectly. Fallback uses name-only parsing.

Permissions: Ensure instance/ and static/uploads/ are writable:
chmod -R 777 instance static/uploads logs


GoFSH/SUSHI Errors: Check ./logs/flask_err.log for ERROR:services:GoFSH failed. Ensure valid FHIR inputs and SUSHI installation:
docker exec -it <container_name> sushi --version



Future Improvements

Validation: Enhance FHIRPath for complex constraints; add API endpoint.
Sorting: Sort IG versions in /view-igs (e.g., ascending).
Duplicate Resolution: Options to keep latest version or merge resources.
Production Database: Support PostgreSQL.
Error Reporting: Detailed validation error paths in the UI.
FSH Enhancements: Add API endpoint for FSH conversion; support inline instance construction.
FHIR Operations: Add complex parameter support (e.g., /$diff with left/right).
Spinner Enhancements: Customize spinner animation speed or size.

Completed Items

Testing suite with 27 cases.
API endpoints for POST /api/import-ig and POST /api/push-ig.
Flexible versioning (-preview, -ballot).
CSRF fixes for forms.
Resource validation UI (alpha).
FSH Converter with advanced GoFSH features and waiting spinner.

Far-Distant Improvements

Cache Service: Use Redis for IG metadata caching.
Database Optimization: Composite index on ProcessedIg.package_name and ProcessedIg.version.

Directory Structure
FHIRFLARE-IG-Toolkit/
├── app.py                              # Main Flask application
├── Build and Run for first time.bat    # Windows script for first-time Docker setup
├── docker-compose.yml                  # Docker Compose configuration
├── Dockerfile                          # Docker configuration
├── forms.py                            # Form definitions
├── LICENSE.md                          # Apache 2.0 License
├── README.md                           # Project documentation
├── requirements.txt                    # Python dependencies
├── Run.bat                             # Windows script for running Docker
├── services.py                         # Logic for IG import, processing, validation, pushing, and FSH conversion
├── supervisord.conf                    # Supervisor configuration
├── hapi-fhir-Setup/
│   ├── README.md                       # HAPI FHIR setup instructions
│   └── target/
│       └── classes/
│           └── application.yaml        # HAPI FHIR configuration
├── instance/
│   ├── fhir_ig.db                      # SQLite database
│   ├── fhir_ig.db.old                  # Database backup
│   └── fhir_packages/                  # Stored IG packages and metadata
│       ├── hl7.fhir.au.base-5.1.0-preview.metadata.json
│       ├── hl7.fhir.au.base-5.1.0-preview.tgz
│       ├── hl7.fhir.au.core-1.1.0-preview.metadata.json
│       ├── hl7.fhir.au.core-1.1.0-preview.tgz
│       ├── hl7.fhir.r4.core-4.0.1.metadata.json
│       ├── hl7.fhir.r4.core-4.0.1.tgz
│       ├── hl7.fhir.uv.extensions.r4-5.2.0.metadata.json
│       ├── hl7.fhir.uv.extensions.r4-5.2.0.tgz
│       ├── hl7.fhir.uv.ipa-1.0.0.metadata.json
│       ├── hl7.fhir.uv.ipa-1.0.0.tgz
│       ├── hl7.fhir.uv.smart-app-launch-2.0.0.metadata.json
│       ├── hl7.fhir.uv.smart-app-launch-2.0.0.tgz
│       ├── hl7.fhir.uv.smart-app-launch-2.1.0.metadata.json
│       ├── hl7.fhir.uv.smart-app-launch-2.1.0.tgz
│       ├── hl7.terminology.r4-5.0.0.metadata.json
│       ├── hl7.terminology.r4-5.0.0.tgz
│       ├── hl7.terminology.r4-6.2.0.metadata.json
│       └── hl7.terminology.r4-6.2.0.tgz
├── logs/
│   ├── flask.log                       # Flask application logs
│   ├── flask_err.log                   # Flask error logs
│   ├── supervisord.log                 # Supervisor logs
│   ├── supervisord.pid                 # Supervisor PID file
│   ├── tomcat.log                      # Tomcat logs for HAPI FHIR
│   └── tomcat_err.log                  # Tomcat error logs
├── static/
│   ├── animations/
│   │   ├── loading-dark.json           # Dark theme spinner animation
│   │   └── loading-light.json          # Light theme spinner animation
│   ├── favicon.ico                     # Application favicon
│   ├── FHIRFLARE.png                   # Application logo
│   ├── js/
│   │   └── lottie-web.min.js           # Lottie library for spinner
│   └── uploads/
│       ├── output.fsh                  # Generated FSH output
│       └── fsh_output/
│           ├── sushi-config.yaml       # SUSHI configuration
│           └── input/
│               └── fsh/
│                   ├── aliases.fsh     # FSH aliases
│                   ├── index.txt       # FSH index
│                   └── instances/
│                       └── banks-mia-leanne.fsh  # Example FSH instance
├── templates/
│   ├── base.html                       # Base template
│   ├── cp_downloaded_igs.html          # UI for managing IGs
│   ├── cp_push_igs.html                # UI for pushing IGs
│   ├── cp_view_processed_ig.html       # UI for viewing processed IGs
│   ├── fhir_ui.html                    # UI for FHIR API explorer
│   ├── fhir_ui_operations.html         # UI for FHIR server operations
│   ├── fsh_converter.html              # UI for FSH conversion
│   ├── import_ig.html                  # UI for importing IGs
│   ├── index.html                      # Homepage
│   ├── validate_sample.html            # UI for validating resources/bundles
│   └── _form_helpers.html              # Form helper macros
├── tests/
│   └── test_app.py                     # Test suite with 27 cases
└── hapi-fhir-jpaserver/                # HAPI FHIR server resources

Contributing

Fork the repository.
Create a feature branch (git checkout -b feature/your-feature).
Commit changes (git commit -m "Add your feature").
Push to your branch (git push origin feature/your-feature).
Open a Pull Request.

Ensure code follows PEP 8 and includes tests in tests/test_app.py.
Troubleshooting

Favicon 404: Clear browser cache or verify /app/static/favicon.ico:
docker exec -it <container_name> curl http://localhost:5000/static/favicon.ico


CSRF Errors: Set FLASK_SECRET_KEY and ensure {{ form.hidden_tag() }} in forms.

Import Fails: Check package name/version and connectivity.

Validation Accuracy: Alpha feature; report issues to GitHub (remove PHI).

Package Parsing: Non-standard .tgz filenamesgrass may parse incorrectly. Fallback uses name-only parsing.

Permissions: Ensure instance/ and static/uploads/ are writable:
chmod -R 777 instance static/uploads logs


GoFSH/SUSHI Errors: Check ./logs/flask_err.log for ERROR:services:GoFSH failed. Ensure valid FHIR inputs and SUSHI installation:
docker exec -it <container_name> sushi --version



License
Licensed under the Apache 2.0 License. See LICENSE.md for details.
