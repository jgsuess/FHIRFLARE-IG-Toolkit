# config.py
# Basic configuration settings for the Flask application

import os

# Determine the base directory of the application (where config.py lives)
basedir = os.path.abspath(os.path.dirname(__file__))

# Define the instance path relative to the base directory
# This seems correct if instance folder is at the same level as config.py/run.py
instance_path = os.path.join(basedir, 'instance')

class Config:
    """Base configuration class."""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'

    # Database configuration (Development/Production)
    # Points to 'instance/app.db' relative to config.py location
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(instance_path, 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False # Disable modification tracking

    # Add other global configuration variables here
    SITE_NAME = "Modular PAS Framework"
    # Add any other default settings your app needs


# --- ADDED Testing Configuration ---
class TestingConfig(Config):
    """Configuration specific to testing."""
    TESTING = True

    # Use a separate database file for tests inside the instance folder
    # Ensures tests don't interfere with development data
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(instance_path, 'test.db')

    # Disable CSRF protection during tests for simplicity
    WTF_CSRF_ENABLED = False

    # Ensure Flask-Login works normally during tests (not disabled)
    LOGIN_DISABLED = False

    # Use a fixed, predictable secret key for testing sessions
    SECRET_KEY = 'testing-secret-key'

    # Inside class TestingConfig(Config):
    SERVER_NAME = 'localhost.test' # Or just 'localhost' is usually fine

# --- You could add other configurations like ProductionConfig(Config) later ---
# class ProductionConfig(Config):
#     SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') # Should be set in prod env
#     # etc...