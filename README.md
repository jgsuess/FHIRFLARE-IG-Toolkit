# FHIRFLARE IG Toolkit

## Overview

The FHIRFLARE IG Toolkit is a Flask-based web application designed to simplify the management, processing, and deployment of FHIR Implementation Guides (IGs). It allows users to import IG packages, process them to extract resource types and profiles, and push them to a FHIR server. The application features a user-friendly interface with a live console for real-time feedback during operations like pushing IGs to a FHIR server.

## Features

- **Import IGs**: Download FHIR IG packages and their dependencies from a package registry.
- **Manage IGs**: View, process, and delete downloaded IGs, with duplicate detection.
- **Process IGs**: Extract resource types, profiles, must-support elements, and examples from IGs.
- **Push IGs**: Upload IG resources to a FHIR server with real-time console output.
- **API Support**: Provides RESTful API endpoints for importing and pushing IGs.
- **Live Console**: Displays real-time logs during push operations.

## Technology Stack

The FHIRFLARE IG Toolkit is built using the following technologies:

- **Python 3.9+**: Core programming language for the backend.
- **Flask 2.0+**: Lightweight web framework for building the application.
- **Flask-SQLAlchemy**: ORM for managing the SQLite database.
- **Flask-WTF**: Handles form creation and CSRF protection.
- **Jinja2**: Templating engine for rendering HTML pages.
- **Bootstrap 5**: Frontend framework for responsive UI design.
- **JavaScript (ES6)**: Client-side scripting for interactive features like the live console.
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
   Set the `FLASK_SECRET_KEY` and `API_KEY` environment variables for security:
   ```bash
   export FLASK_SECRET_KEY='your-secure-secret-key'
   export API_KEY='your-api-key'
   ```

4. **Run the Application Locally**:
   Start the Flask development server:
   ```bash
   flask run
   ```
   The application will be available at `http://localhost:5000`.

5. **Run with Docker**:
   Build and run the application using Docker:
   ```bash
   docker build -t flare-fhir-ig-toolkit .
   docker run -p 5000:5000 -e FLASK_SECRET_KEY='your-secure-secret-key' -e API_KEY='your-api-key' -v $(pwd)/instance:/app/instance flare-fhir-ig-toolkit
   ```
   Access the application at `http://localhost:5000`.

## Usage

1. **Import an IG**:
   - Navigate to the "Import IG" tab.
   - Enter a package name (e.g., `hl7.fhir.us.core`) and version (e.g., `3.1.1`).
   - Click "Fetch & Download IG" to download the package and its dependencies.

2. **Manage IGs**:
   - Go to the "Manage FHIR Packages" tab to view downloaded IGs.
   - Process, delete, or view details of IGs. Duplicates are highlighted for resolution.

3. **Push IGs to a FHIR Server**:
   - Navigate to the "Push IGs" tab.
   - Select a package, enter a FHIR server URL (e.g., `http://hapi.fhir.org/baseR4`), and choose whether to include dependencies.
   - Click "Push to FHIR Server" to upload resources, with progress shown in the live console.

4. **API Usage**:
   - **Import IG**: `POST /api/import-ig`
     ```bash
     curl -X POST http://localhost:5000/api/import-ig \
     -H "Content-Type: application/json" \
     -d '{"package_name": "hl7.fhir.us.core", "version": "3.1.1", "api_key": "your-api-key"}'
     ```
   - **Push IG**: `POST /api/push-ig`
     ```bash
     curl -X POST http://localhost:5000/api/push-ig \
     -H "Content-Type: application/json" \
     -H "Accept: application/x-ndjson" \
     -d '{"package_name": "hl7.fhir.us.core", "version": "3.1.1", "fhir_server_url": "http://hapi.fhir.org/baseR4", "include_dependencies": true, "api_key": "your-api-key"}'
     ```

## Testing

The project includes a comprehensive test suite to ensure the reliability of the application. Tests cover the UI, API endpoints, database operations, file handling, and security features like CSRF protection.

### Test Prerequisites

- **pytest**: For running the tests.
- **unittest-mock**: For mocking dependencies in tests.

Install the test dependencies:
```bash
pip install pytest unittest-mock
```

### Running Tests

1. **Navigate to the Project Root**:
   ```bash
   cd /path/to/fhirflare-ig-toolkit
   ```

2. **Run the Tests**:
   Run the test suite using `pytest`:
   ```bash
   pytest tests/test_app.py -v
   ```
   - The `-v` flag provides verbose output, showing the status of each test.
   - Alternatively, run from the `tests/` directory with:
     ```bash
     cd tests/
     pytest test_app.py -v
     ```

### Test Coverage

The test suite includes 27 test cases covering the following areas:

- **UI Pages**:
  - Homepage (`/`): Rendering and content.
  - Import IG page (`/import-ig`): Form rendering and submission (success, failure, invalid input).
  - Manage IGs page (`/view-igs`): Rendering with and without packages.
  - Push IGs page (`/push-igs`): Rendering and live console.
  - View Processed IG page (`/view-ig/<id>`): Rendering processed IG details.

- **API Endpoints**:
  - `POST /api/import-ig`: Success, invalid API key, missing parameters.
  - `POST /api/push-ig`: Success, invalid API key, package not found.
  - `GET /get-structure`: Fetching structure definitions (success, not found).
  - `GET /get-example`: Fetching example content (success, invalid path).

- **Database Operations**:
  - Processing IGs: Storing processed IG data in the database.
  - Unloading IGs: Removing processed IG records.
  - Viewing processed IGs: Retrieving and displaying processed IG data.

- **File Operations**:
  - Processing IG packages: Extracting data from `.tgz` files.
  - Deleting IG packages: Removing `.tgz` files from the filesystem.

- **Security**:
  - Secret Key: CSRF protection for form submissions.
  - Flash Messages: Session integrity for flash messages.

### Example Test Output

A successful test run will look like this:
```
================================================================ test session starts =================================================================
platform linux -- Python 3.9.22, pytest-8.3.5, pluggy-1.5.0 -- /usr/local/bin/python3.9
cachedir: .pytest_cache
rootdir: /app/tests
collected 27 items

test_app.py::TestFHIRFlareIGToolkit::test_homepage PASSED                         [  3%]
test_app.py::TestFHIRFlareIGToolkit::test_import_ig_page PASSED                   [  7%]
test_app.py::TestFHIRFlareIGToolkit::test_import_ig_success PASSED                [ 11%]
test_app.py::TestFHIRFlareIGToolkit::test_import_ig_failure PASSED                [ 14%]
test_app.py::TestFHIRFlareIGToolkit::test_import_ig_invalid_input PASSED          [ 18%]
test_app.py::TestFHIRFlareIGToolkit::test_view_igs_no_packages PASSED             [ 22%]
test_app.py::TestFHIRFlareIGToolkit::test_view_igs_with_packages PASSED           [ 25%]
test_app.py::TestFHIRFlareIGToolkit::test_process_ig_success PASSED               [ 29%]
test_app.py::TestFHIRFlareIGToolkit::test_process_ig_invalid_file PASSED          [ 33%]
test_app.py::TestFHIRFlareIGToolkit::test_delete_ig_success PASSED                [ 37%]
test_app.py::TestFHIRFlareIGToolkit::test_delete_ig_file_not_found PASSED         [ 40%]
test_app.py::TestFHIRFlareIGToolkit::test_unload_ig_success PASSED                [ 44%]
test_app.py::TestFHIRFlareIGToolkit::test_unload_ig_invalid_id PASSED             [ 48%]
test_app.py::TestFHIRFlareIGToolkit::test_view_processed_ig PASSED                [ 51%]
test_app.py::TestFHIRFlareIGToolkit::test_push_igs_page PASSED                    [ 55%]
test_app.py::TestFHIRFlareIGToolkit::test_api_import_ig_success PASSED            [ 59%]
test_app.py::TestFHIRFlareIGToolkit::test_api_import_ig_invalid_api_key PASSED    [ 62%]
test_app.py::TestFHIRFlareIGToolkit::test_api_import_ig_missing_params PASSED     [ 66%]
test_app.py::TestFHIRFlareIGToolkit::test_api_push_ig_success PASSED              [ 70%]
test_app.py::TestFHIRFlareIGToolkit::test_api_push_ig_invalid_api_key PASSED      [ 74%]
test_app.py::TestFHIRFlareIGToolkit::test_api_push_ig_package_not_found PASSED    [ 77%]
test_app.py::TestFHIRFlareIGToolkit::test_secret_key_csrf PASSED                  [ 81%]
test_app.py::TestFHIRFlareIGToolkit::test_secret_key_flash_messages PASSED        [ 85%]
test_app.py::TestFHIRFlareIGToolkit::test_get_structure_definition_success PASSED [ 88%]
test_app.py::TestFHIRFlareIGToolkit::test_get_structure_definition_not_found PASSED [ 92%]
test_app.py::TestFHIRFlareIGToolkit::test_get_example_content_success PASSED      [ 96%]
test_app.py::TestFHIRFlareIGToolkit::test_get_example_content_invalid_path PASSED [100%]

============================================================= 27 passed in 1.23s ==============================================================
```

### Troubleshooting Tests

- **ModuleNotFoundError**: If you encounter `ModuleNotFoundError: No module named 'app'`, ensure you’re running the tests from the project root (`/app/`) or that the `sys.path` modification in `test_app.py` is correctly adding the parent directory.
- **Missing Templates**: If tests fail with `TemplateNotFound`, ensure all required templates (`index.html`, `import_ig.html`, etc.) are in the `/app/templates/` directory.
- **Missing Dependencies**: If tests fail due to missing `services.py` or its functions, ensure `services.py` is present in `/app/` and contains the required functions (`import_package_and_dependencies`, `process_package_file`, etc.).

## Directory Structure

- `app.py`: Main Flask application file.
- `services.py`: Business logic for importing, processing, and pushing IGs.
- `templates/`: HTML templates for the UI.
- `instance/`: Directory for SQLite database and downloaded packages.
- `tests/`: Directory for test files.
  - `test_app.py`: Test suite for the application.
- `requirements.txt`: List of Python dependencies.
- `Dockerfile`: Docker configuration for containerized deployment.

## Contributing

Contributions are welcome! Please fork the repository, create a feature branch, and submit a pull request with your changes.

## License

This project is licensed under the Apache 2.0 License. See the `LICENSE` file for details.