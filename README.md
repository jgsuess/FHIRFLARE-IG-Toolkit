# FHIRFLARE IG Toolkit

## Overview

FHIRFLARE IG Toolkit is a Flask-based web application designed to simplify the management of FHIR Implementation Guides (IGs). It allows users to import, process, view, and manage FHIR packages, with features to handle duplicate dependencies and visualize processed IGs. The toolkit is built to assist developers, researchers, and healthcare professionals working with FHIR standards.

This tool was initially developed as an IG package viewer within a larger project I was building. As the requirements expanded and it became clear there was a strong need for a lightweight, purpose-built solution to interact with IG packages, I decided to decouple it from the original codebase and release it as a standalone open-source utility for broader community use.

### Key Features
- **Import FHIR Packages**: Download FHIR IGs and their dependencies by specifying package names and versions.
- **Manage Duplicates**: Detect and highlight duplicate packages with different versions, using color-coded indicators.
- **Process IGs**: Extract and process FHIR resources, including structure definitions, must-support elements, and examples.
- **View Details**: Explore processed IGs with detailed views of resource types and examples.
- **Database Integration**: Store processed IGs in a SQLite database for persistence.
- **User-Friendly Interface**: Built with Bootstrap for a responsive and intuitive UI.

## Technology Stack

This application is built using the following technologies:

* **Backend:**
    * **Python:** The primary programming language.
    * **Flask:** A lightweight web framework for building the application.
    * **SQLAlchemy:** An ORM (Object-Relational Mapper) for interacting with the database.
* **Frontend:**
    * **HTML:** For structuring the web pages.
    * **CSS:** Styling is primarily provided by Bootstrap.
    * **Bootstrap 5.3.3:** A CSS framework for responsive and consistent design.
    * **Bootstrap Icons 1.11.3:** A library of icons for use within the user interface.
    * **JavaScript:** For client-side interactivity, particularly in the IG details view.
* **Data Storage:**
    * **SQLite:** (Example -  *You should specify your actual database here if different*) A lightweight, file-based database.
* **Other:**
    * **tarfile:** Python's built-in module for working with tar archives.
    * **requests:** A Python library for making HTTP requests.
    * **json:** Python's built-in module for working with JSON data.

## Prerequisites

Before setting up the project, ensure you have the following installed:
- **Python 3.8+**
- **Docker** (optional, for containerized deployment)
- **pip** (Python package manager)

## Setup Instructions

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/FLARE-FHIR-IG-Toolkit.git
cd FLARE-FHIR-IG-Toolkit
```

### 2. Create a Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
Install the required Python packages using `pip`:
```bash
pip install -r requirements.txt
```

If you don’t have a `requirements.txt` file, you can install the core dependencies manually:
```bash
pip install flask flask-sqlalchemy flask-wtf
```

### 4. Set Up the Instance Directory
The application uses an `instance` directory to store the SQLite database and FHIR packages. Create this directory and set appropriate permissions:
```bash
mkdir instance
mkdir instance/fhir_packages
chmod -R 777 instance  # Ensure the directory is writable
```

### 5. Initialize the Database
The application uses a SQLite database (`instance/fhir_ig.db`) to store processed IGs. The database is automatically created when you first run the application, but you can also initialize it manually:
```bash
python -c "from app import db; db.create_all()"
```

### 6. Run the Application Locally
Start the Flask development server:
```bash
python app.py
```

The application will be available at `http://localhost:5000`.

### 7. (Optional) Run with Docker
If you prefer to run the application in a Docker container:
```bash
docker build -t flare-fhir-ig-toolkit .
docker run -p 5000:5000 -v $(pwd)/instance:/app/instance flare-fhir-ig-toolkit
```

The application will be available at `http://localhost:5000`.

## Database Management

The application uses a SQLite database (`instance/fhir_ig.db`) to store processed IGs. Below are steps to create, purge, and recreate the database.

### Creating the Database
The database is automatically created when you first run the application. To create it manually:
```bash
python -c "from app import db; db.create_all()"
```
This will create `instance/fhir_ig.db` with the necessary tables.

### Purging the Database
To purge the database (delete all data while keeping the schema):
1. Stop the application if it’s running.
2. Run the following command to drop all tables and recreate them:
   ```bash
   python -c "from app import db; db.drop_all(); db.create_all()"
   ```
3. Alternatively, you can delete the database file and recreate it:
   ```bash
   rm instance/fhir_ig.db
   python -c "from app import db; db.create_all()"
   ```

### Recreating the Database
To completely recreate the database (e.g., after making schema changes):
1. Stop the application if it’s running.
2. Delete the existing database file:
   ```bash
   rm instance/fhir_ig.db
   ```
3. Recreate the database:
   ```bash
   python -c "from app import db; db.create_all()"
   ```
4. Restart the application:
   ```bash
   python app.py
   ```

**Note**: Ensure the `instance` directory has write permissions (`chmod -R 777 instance`) to avoid permission errors when creating or modifying the database.

## Usage

### Importing FHIR Packages
1. Navigate to the "Import IGs" page (`/import-ig`).
2. Enter the package name (e.g., `hl7.fhir.us.core`) and version (e.g., `1.0.0` or `current`).
3. Click "Fetch & Download IG" to download the package and its dependencies.
4. You’ll be redirected to the "Manage FHIR Packages" page to view the downloaded packages.

### Managing FHIR Packages
- **View Downloaded Packages**: The "Manage FHIR Packages" page (`/view-igs`) lists all downloaded packages.
- **Handle Duplicates**: Duplicate packages with different versions are highlighted with color-coded rows (e.g., yellow for one group, light blue for another).
- **Process Packages**: Click "Process" to extract and store package details in the database.
- **Delete Packages**: Click "Delete" to remove a package from the filesystem.

### Viewing Processed IGs
- Processed packages are listed in the "Processed Packages" section.
- Click "View" to see detailed information about a processed IG, including resource types and examples.
- Click "Unload" to remove a processed IG from the database.

## Project Structure

```
FLARE-FHIR-IG-Toolkit/
├── app.py                  # Main Flask application
├── instance/               # Directory for SQLite database and FHIR packages
│   ├── fhir_ig.db          # SQLite database
│   └── fhir_packages/      # Directory for downloaded FHIR packages
├── static/                 # Static files (e.g., favicon.ico, FHIRFLARE.png)
│   ├── FHIRFLARE.png
│   └── favicon.ico
├── templates/              # HTML templates
│   ├── base.html
│   ├── cp_downloaded_igs.html
│   ├── cp_view_processed_ig.html
│   ├── import_ig.html
│   └── index.html
├── services.py             # Helper functions for processing FHIR packages
└── README.md               # Project documentation
```

## Development Notes

### Background
The FHIRFLARE IG Toolkit was developed to address the need for a user-friendly tool to manage FHIR Implementation Guides. The project focuses on providing a seamless experience for importing, processing, and analyzing FHIR packages, with a particular emphasis on handling duplicate dependencies—a common challenge in FHIR development.

### Technical Decisions
- **Flask**: Chosen for its lightweight and flexible nature, making it ideal for a small to medium-sized web application.
- **SQLite**: Used as the database for simplicity and ease of setup. For production use, consider switching to a more robust database like PostgreSQL.
- **Bootstrap**: Integrated for a responsive and professional UI, with custom CSS to handle duplicate package highlighting.
- **Docker Support**: Added to simplify deployment and ensure consistency across development and production environments.

### Known Issues and Workarounds
- **Bootstrap CSS Conflicts**: Early versions of the application had issues with Bootstrap’s table background styles (`--bs-table-bg`) overriding custom row colors for duplicate packages. This was resolved by setting `--bs-table-bg` to `transparent` for the affected table (see `templates/cp_downloaded_igs.html`).
- **Database Permissions**: The `instance` directory must be writable by the application. If you encounter permission errors, ensure the directory has the correct permissions (`chmod -R 777 instance`).
- **Package Parsing**: Some FHIR package filenames may not follow the expected `name-version.tgz` format, leading to parsing issues. The application includes a fallback to treat such files as name-only packages, but this may need further refinement.

### Future Improvements
- **Sorting Versions**: Add sorting for package versions in the "Manage FHIR Packages" view to display them in a consistent order (e.g., ascending or descending).
- **Advanced Duplicate Handling**: Implement options to resolve duplicates (e.g., keep the latest version, merge resources).
- **Production Database**: Support for PostgreSQL or MySQL for better scalability in production environments.
- **Testing**: Add unit tests using `pytest` to cover core functionality, especially package processing and database operations.
- **Inbound API for IG Packages**: Develop API endpoints to allow external tools to push IG packages to FHIRFLARE. The API should automatically resolve dependencies, return a list of dependencies, and identify any duplicate dependencies. For example:
  - Endpoint: `POST /api/import-ig`
  - Request: `{ "package_name": "hl7.fhir.us.core", "version": "1.0.0" }`
  - Response: `{ "status": "success", "dependencies": ["hl7.fhir.r4.core#4.0.1"], "duplicates": ["hl7.fhir.r4.core#4.0.1 (already exists as 5.0.0)"] }`
- **Outbound API for Pushing IGs to FHIR Servers**: Create an outbound API to push a chosen IG (with its dependencies) to a FHIR server, or allow pushing a single IG without dependencies. The API should process the server’s responses and provide feedback. For example:
  - Endpoint: `POST /api/push-ig`
  - Request: `{ "package_name": "hl7.fhir.us.core", "version": "1.0.0", "fhir_server_url": "https://fhir-server.example.com", "include_dependencies": true }`
  - Response: `{ "status": "success", "pushed_packages": ["hl7.fhir.us.core#1.0.0", "hl7.fhir.r4.core#4.0.1"], "server_response": "Resources uploaded successfully" }`
- **Far-Distant Improvements**:
  - **Cache Service for IGs**: Implement a cache service to store all IGs, allowing for quick querying of package metadata without reprocessing. This could use an in-memory store like Redis to improve performance.
  - **Database Index Optimization**: Modify the database structure to use a composite index on `package_name` and `version` (e.g., `ProcessedIg.package_name + ProcessedIg.version` as a unique key). This would allow the `/view-igs` page and API endpoints to directly query specific packages (e.g., `/api/ig/hl7.fhir.us.core/1.0.0`) without scanning the entire table.

## Contributing

Contributions are welcome! To contribute:
1. Fork the repository.
2. Create a new branch (`git checkout -b feature/your-feature`).
3. Make your changes and commit them (`git commit -m "Add your feature"`).
4. Push to your branch (`git push origin feature/your-feature`).
5. Open a Pull Request.

Please ensure your code follows the project’s coding style and includes appropriate tests.

## Troubleshooting

- **Database Issues**: If the SQLite database (`instance/fhir_ig.db`) cannot be created, ensure the `instance` directory is writable. You may need to adjust permissions (`chmod -R 777 instance`).
- **Package Download Fails**: Verify your internet connection and ensure the package name and version are correct.
- **Colors Not Displaying**: If table row colors for duplicates are not showing, inspect the page with browser developer tools (F12) to check for CSS conflicts with Bootstrap.

## License

This project is licensed under the Apache License, Version 2.0.  See the [LICENSE.md](LICENSE.md) file for details.

## Contact

For questions or support, please open an issue on GitHub or contact the maintainers at [your-email@example.com](mailto:your-email@example.com).
