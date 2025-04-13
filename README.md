# FHIRFLARE IG Toolkit

## Overview

The FHIRFLARE IG Toolkit is a Flask-based web application designed to simplify the management, processing, validation, and deployment of FHIR Implementation Guides (IGs). It allows users to import IG packages, process them to extract resource types and profiles, validate FHIR resources or bundles against IGs, and push IGs to a FHIR server. The application features a user-friendly interface with a live console for real-time feedback during operations like pushing IGs or validating resources.

## Features

- **Import IGs**: Download FHIR IG packages and their dependencies from a package registry, supporting flexible version formats (e.g., `1.2.3`, `1.1.0-preview`, `1.1.2-ballot`, `current`).
- **Manage IGs**: View, process, unload, or delete downloaded IGs, with duplicate detection and resolution.
- **Process IGs**: Extract resource types, profiles, must-support elements, examples, and profile relationships (`compliesWithProfile` and `imposeProfile`) from IGs.
- **Validate FHIR Resources/Bundles**: Validate single FHIR resources or bundles against selected IGs, with detailed error and warning reports (alpha feature, work in progress).
- **Push IGs**: Upload IG resources to a FHIR server with real-time console output, including validation against imposed profiles.
- **Profile Relationships**: Support for `structuredefinition-compliesWithProfile` and `structuredefinition-imposeProfile` extensions, with validation and UI display.
- **API Support**: RESTful API endpoints for importing, pushing, and validating IGs, including profile relationship metadata.
- **Live Console**: Displays real-time logs during push and validation operations.
- **Configurable Behavior**: Options to enable/disable imposed profile validation and UI display of profile relationships.

## Technology Stack

The FHIRFLARE IG Toolkit is built using the following technologies:

- **Python 3.9+**: Core programming language for the backend.
- **Flask 2.0+**: Lightweight web framework for building the application.
- **Flask-SQLAlchemy**: ORM for managing the SQLite database.
- **Flask-WTF**: Handles form creation, validation, and CSRF protection.
- **Jinja2**: Templating engine for rendering HTML pages.
- **Bootstrap 5**: Frontend framework for responsive UI design.
- **JavaScript (ES6)**: Client-side scripting for interactive features like the live console and JSON validation preview.
- **SQLite**: Lightweight database for storing processed IG metadata.
- **Docker**: Containerization for consistent deployment.
- **Requests**: Python library for making HTTP requests to FHIR servers.
- **Tarfile**: Python library for handling `.tgz` package files.
- **Logging**: Python's built-in logging for debugging and monitoring.

## Prerequisites

- **Python 3.9+**: Ensure Python is installed on your system.
- **Docker**: Required for containerized deployment.
- **pip**: Python package manager for installing dependencies.

## Setup Instructions

1. **Clone the Repository**:
   ```bash
   git clone <repository-url>
   cd fhirflare-ig-toolkit
   ```

2. **Install Dependencies**:
   Create a virtual environment and install the required packages:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Set Environment Variables**:
   Set the `FLASK_SECRET_KEY` and `API_KEY` for security and CSRF protection:
   ```bash
   export FLASK_SECRET_KEY='your-secure-secret-key'
   export API_KEY='your-api-key'
   ```

4. **Initialize the Database**:
   Ensure the `instance` directory is writable for SQLite:
   ```bash
   mkdir -p instance
   chmod -R 777 instance
   ```

5. **Run the Application Locally**:
   Start the Flask development server:
   ```bash
   export FLASK_APP=app.py
   flask run
   ```
   The application will be available at `http://localhost:5000`.

6. **Run with Docker**:
   Build and run the application using Docker, mounting the `instance` directory for persistence:
   ```bash
   docker build -t flare-fhir-ig-toolkit .
   docker run -p 5000:5000 -e FLASK_SECRET_KEY='your-secure-secret-key' -e API_KEY='your-api-key' -v $(pwd)/instance:/app/instance flare-fhir-ig-toolkit
   ```
   Access the application at `http://localhost:5000`.

## Usage

1. **Import an IG**:
   - Navigate to the "Import IG" tab.
   - Enter a package name (e.g., `hl7.fhir.au.core`) and version (e.g., `1.1.0-preview`, `1.1.2-ballot`, or `current`).
   - Choose a dependency mode (e.g., Recursive, Tree Shaking).
   - Click "Import" to download the package and its dependencies.

2. **Manage IGs**:
   - Go to the "Manage FHIR Packages" tab to view downloaded and processed IGs.
   - Process IGs to extract metadata, unload processed IGs from the database, or delete packages from the filesystem.
   - Duplicates are highlighted for resolution (e.g., multiple versions of the same package).

3. **View Processed IGs**:
   - After processing an IG, view its details, including resource types, profiles, must-support elements, examples, and profile relationships (`compliesWithProfile` and `imposeProfile`).
   - Profile relationships are displayed if enabled via `DISPLAY_PROFILE_RELATIONSHIPS`.

4. **Validate FHIR Resources/Bundles**:
   - Navigate to the "Validate FHIR Sample" tab.
   - Select or enter a package (e.g., `hl7.fhir.au.core#1.1.0-preview`).
   - Choose validation mode (Single Resource or Bundle).
   - Paste FHIR JSON (e.g., a Patient or AllergyIntolerance resource).
   - Submit to validate, viewing errors and warnings in the report.
   - Note: Validation is in alpha; report issues to GitHub (remove PHI).

5. **Push IGs to a FHIR Server**:
   - Navigate to the "Push IGs" tab.
   - Select a package, enter a FHIR server URL (e.g., `http://hapi.fhir.org/baseR4`), and choose whether to include dependencies.
   - Click "Push to FHIR Server" to upload resources, with validation against imposed profiles (if enabled via `VALIDATE_IMPOSED_PROFILES`) and progress shown in the live console.

6. **API Usage**:
   - **Import IG**: `POST /api/import-ig`
     ```bash
     curl -X POST http://localhost:5000/api/import-ig \
     -H "Content-Type: application/json" \
     -d '{"package_name": "hl7.fhir.au.core", "version": "1.1.0-preview", "api_key": "your-api-key"}'
     ```
     Response includes `complies_with_profiles`, `imposed_profiles`, and duplicates.
   - **Push IG**: `POST /api/push-ig`
     ```bash
     curl -X POST http://localhost:5000/api/push-ig \
     -H "Content-Type: application/json" \
     -H "Accept: application/x-ndjson" \
     -d '{"package_name": "hl7.fhir.au.core", "version": "1.1.0-preview", "fhir_server_url": "http://hapi.fhir.org/baseR4", "include_dependencies": true, "api_key": "your-api-key"}'
     ```
     Resources are validated against imposed profiles before pushing.
   - **Validate Resource/Bundle**: Not yet exposed via API; use the UI at `/validate-sample`.

## Configuration Options

- **`VALIDATE_IMPOSED_PROFILES`**: Set to `True` (default) to validate resources against imposed profiles during push operations. Set to `False` to skip:
  ```python
  app.config['VALIDATE_IMPOSED_PROFILES'] = False
  ```
- **`DISPLAY_PROFILE_RELATIONSHIPS`**: Set to `True` (default) to show `compliesWithProfile` and `imposeProfile` in the UI. Set to `False` to hide:
  ```python
  app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = False
  ```
- **`FHIR_PACKAGES_DIR`**: Directory for storing `.tgz` packages and metadata (default: `/app/instance/fhir_packages`).
- **`SECRET_KEY`**: Required for CSRF protection and session security:
  ```python
  app.config['SECRET_KEY'] = 'your-secure-secret-key'
  ```

## Testing

The project includes a test suite to ensure reliability, covering UI, API, database, file operations, and security features.

### Test Prerequisites

- **pytest**: For running tests.
- **unittest-mock**: For mocking dependencies.

Install test dependencies:
```bash
pip install pytest pytest-mock
```

### Running Tests

1. **Navigate to Project Root**:
   ```bash
   cd /path/to/fhirflare-ig-toolkit
   ```

2. **Run Tests**:
   ```bash
   pytest tests/test_app.py -v
   ```
   - `-v` provides verbose output.
   - Tests are in `tests/test_app.py`, covering 27 cases.

### Test Coverage

Tests include:
- **UI Pages**:
  - Homepage, Import IG, Manage IGs, Push IGs, Validate Sample, View Processed IG.
  - Form rendering, submissions, and error handling (e.g., invalid JSON, CSRF).
- **API Endpoints**:
  - `POST /api/import-ig`: Success, invalid key, duplicates, profile relationships.
  - `POST /api/push-ig`: Success, validation, errors.
  - `GET /get-structure`, `GET /get-example`: Success and failure cases.
- **Database**:
  - Processing, unloading, and viewing IGs.
- **File Operations**:
  - Package processing, deletion.
- **Security**:
  - CSRF protection, flash messages, secret key.

### Example Test Output

```
================================================================ test session starts =================================================================
platform linux -- Python 3.9.22, pytest-8.3.5, pluggy-1.5.0
rootdir: /app/tests
collected 27 items

test_app.py::TestFHIRFlareIGToolkit::test_homepage PASSED                         [  3%]
test_app.py::TestFHIRFlareIGToolkit::test_import_ig_page PASSED                   [  7%]
...
test_app.py::TestFHIRFlareIGToolkit::test_validate_sample_page PASSED             [ 85%]
test_app.py::TestFHIRFlareIGToolkit::test_validate_sample_success PASSED          [ 88%]
...
============================================================= 27 passed in 1.23s ==============================================================
```

### Troubleshooting Tests

- **ModuleNotFoundError**: Ensure `app.py`, `services.py`, and `forms.py` are in `/app/`. Run tests from the project root.
- **TemplateNotFound**: Verify templates (`validate_sample.html`, etc.) are in `/app/templates/`.
- **Database Errors**: Ensure `instance/fhir_ig.db` is writable (`chmod 777 instance`).
- **Mock Failures**: Check `tests/test_app.py` for correct mocking of `services.py` functions.

## Development Notes

### Background

The toolkit addresses the need for a user-friendly FHIR IG management tool, with recent enhancements for resource validation and flexible version handling (e.g., `1.1.0-preview`).

### Technical Decisions

- **Flask**: Lightweight and flexible for web development.
- **SQLite**: Simple for development; consider PostgreSQL for production.
- **Bootstrap 5**: Responsive UI with custom CSS for duplicate highlighting.
- **Flask-WTF**: Robust form validation and CSRF protection.
- **Docker**: Ensures consistent deployment.
- **Flexible Versioning**: Supports non-standard version formats for FHIR IGs (e.g., `-preview`, `-ballot`).
- **Validation**: Alpha feature for validating FHIR resources/bundles, with ongoing improvements to FHIRPath handling.

### Recent Updates

- **Version Format Support**: Added support for flexible IG version formats (e.g., `1.1.0-preview`, `1.1.2-ballot`, `current`) in `forms.py`.
- **CSRF Protection**: Fixed missing CSRF tokens in `cp_downloaded_igs.html` and `cp_push_igs.html`, ensuring secure form submissions.
- **Form Handling**: Updated `validate_sample.html` and `app.py` to use `version` instead of `package_version`, aligning with `ValidationForm`.
- **Validation Feature**: Added alpha support for validating FHIR resources/bundles against IGs, with error/warning reports (UI only).

### Known Issues and Workarounds

- **CSRF Errors**: Ensure `SECRET_KEY` is set and forms include `{{ form.csrf_token }}`. Check logs for `flask_wtf.csrf` errors.
- **Version Validation**: Previously restricted to `x.y.z`; now supports suffixes like `-preview`. Report any import issues.
- **Validation Accuracy**: Resource validation is alpha; FHIRPath logic may miss complex constraints. Report anomalies to GitHub (remove PHI).
- **Package Parsing**: Non-standard `.tgz` filenames may parse incorrectly. Fallback treats them as name-only packages.
- **Permissions**: Ensure `instance` directory is writable (`chmod -R 777 instance`) to avoid database or file errors.

### Future Improvements

- [ ] **Sorting Versions**: Sort package versions in `/view-igs` (e.g., ascending).
- [ ] **Duplicate Resolution**: Add options to keep latest version or merge resources.
- [ ] **Production Database**: Support PostgreSQL for scalability.
- [ ] **Validation Enhancements**: Improve FHIRPath handling for complex constraints; add API endpoint for validation.
- [ ] **Error Reporting**: Enhance UI feedback for validation errors with specific element paths.

**Completed Items**:
- ~~Testing: Comprehensive test suite for UI, API, and database.~~
- ~~Inbound API: `POST /api/import-ig` with dependency and profile support.~~
- ~~Outbound API: `POST /api/push-ig` with validation and feedback.~~
- ~~Flexible Versioning: Support for `-preview`, `-ballot`, etc.~~
- ~~CSRF Fixes: Secured forms in `cp_downloaded_igs.html`, `cp_push_igs.html`.~~
- ~~Resource Validation: UI for validating resources/bundles (alpha).~~

### Far-Distant Improvements

- **Cache Service**: Use Redis to cache IG metadata for faster queries.
- **Database Optimization**: Add composite index on `ProcessedIg.package_name` and `ProcessedIg.version` for efficient lookups.

## Directory Structure

- `app.py`: Main Flask application.
- `services.py`: Logic for IG import, processing, validation, and pushing.
- `forms.py`: Form definitions for import and validation.
- `templates/`: HTML templates (`validate_sample.html`, `cp_downloaded_igs.html`, etc.).
- `instance/`: SQLite database (`fhir_ig.db`) and packages (`fhir_packages/`).
- `tests/test_app.py`: Test suite with 27 cases.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: Docker configuration.

## Contributing

Contributions are welcome! To contribute:
1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/your-feature`).
3. Commit changes (`git commit -m "Add your feature"`).
4. Push to your branch (`git push origin feature/your-feature`).
5. Open a Pull Request.

Ensure code follows style guidelines and includes tests.

## Troubleshooting

- **CSRF Errors**: Verify `SECRET_KEY` is set and forms include `{{ form.csrf_token }}`. Check browser DevTools for POST data.
- **Import Fails**: Confirm package name/version (e.g., `hl7.fhir.au.core#1.1.0-preview`) and internet connectivity.
- **Validation Errors**: Alpha feature; report issues to GitHub with JSON samples (remove PHI).
- **Database Issues**: Ensure `instance/fhir_ig.db` is writable (`chmod 777 instance`).
- **Docker Volume**: Mount `instance` directory to persist data:
  ```bash
  docker run -v $(pwd)/instance:/app/instance ...
  ```

## License

Licensed under the Apache 2.0 License. See `LICENSE` for details.