# tests/conftest.py

import pytest
import os
from app import create_app, db, discover_and_register_modules # Keep import
from config import TestingConfig
from app.models import User
from tests.test_control_panel import create_test_user, login # Or move helpers to a shared file


# Determine the instance path for removing the test DB later
instance_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'instance')
TEST_DB_PATH = os.path.join(instance_path, 'test.db')

# --- Use 'function' scope for better isolation ---
@pytest.fixture(scope='function')
def app():
    """
    Function-scoped test Flask application. Configured for testing.
    Handles creation and teardown of the test database FOR EACH TEST.
    Initializes modules AFTER database creation.
    """
    # Create app with TestingConfig, skip module init initially
    app = create_app(TestingConfig, init_modules=False)

    # Establish an application context before accessing db
    with app.app_context():
        print(f"\n--- Setting up test database for function at {app.config['SQLALCHEMY_DATABASE_URI']} ---")
        # Ensure instance folder exists
        try:
            os.makedirs(app.instance_path)
        except OSError:
            pass # Already exists

        # Remove old test database file if it exists (paranoid check)
        if os.path.exists(TEST_DB_PATH):
            # print(f"--- Removing old test database file: {TEST_DB_PATH} ---") # Can be noisy
            os.remove(TEST_DB_PATH)

        # Create tables based on models
        db.create_all()
        print("--- Test database tables created ---") # Keep this print for confirmation

        # --- FIX: Run discovery AFTER DB setup ---
        print("--- Initializing modules after DB setup ---") # Keep this print
        discover_and_register_modules(app) # <-- UNCOMMENTED / ADDED THIS LINE
        # --- End Module Discovery ---

        # Yield the app instance for use in the single test function
        yield app

        # --- Teardown (runs after each test function) ---
        # print("\n--- Tearing down test database for function ---") # Can be noisy
        db.session.remove()
        db.drop_all()
        # Optional: Remove the test database file after test run
        # if os.path.exists(TEST_DB_PATH):
        #     os.remove(TEST_DB_PATH)

# --- Use 'function' scope ---
@pytest.fixture(scope='function')
def client(app):
    """
    Provides a Flask test client for the function-scoped app.
    """
    return app.test_client()

# --- Use 'function' scope ---
@pytest.fixture(scope='function')
def runner(app):
    """
    Provides a Flask test CLI runner for the function-scoped app.
    """
    return app.test_cli_runner()

# --- ADDED Fixture for Logged-In Admin Client ---
@pytest.fixture(scope='function')
def admin_client(client, app):
    """
    Provides a test client already logged in as a pre-created admin user.
    Uses the function-scoped 'client' and 'app' fixtures.
    """
    admin_username = "fixture_admin"
    admin_email = "fixture_admin@example.com" # Unique email
    admin_password = "password"

    # Create admin user within the app context provided by the 'app' fixture
    with app.app_context():
        create_test_user(
            username=admin_username,
            email=admin_email,
            password=admin_password,
            role="admin"
        )

    # Log the admin user in using the 'client' fixture
    login_res = login(client, admin_username, admin_password)
    # Basic check to ensure login likely succeeded (redirect expected)
    if login_res.status_code != 302:
         pytest.fail("Admin login failed during fixture setup.")

    # Yield the already-logged-in client for the test
    yield client

    # Teardown (logout) is optional as function scope cleans up,
    # but can be added for explicit cleanup if needed.
    # client.get(url_for('auth.logout'))

# --- Potential Future Fixtures ---
# (Keep commented out potential session fixture)