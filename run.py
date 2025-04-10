# run.py
# Main entry point to start the Flask development server.

import os
from dotenv import load_dotenv

# Load environment variables from .env file, if it exists
# Useful for storing sensitive info like SECRET_KEY or DATABASE_URL locally
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
    print("Loaded environment variables from .env file.")
else:
    print(".env file not found, using default config or environment variables.")


# Import the application factory function (from app/__init__.py, which we'll create content for next)
# We assume the 'app' package exists with an __init__.py containing create_app()
try:
    from app import create_app
except ImportError as e:
     # Provide a helpful message if the app structure isn't ready yet
     print(f"Error importing create_app: {e}")
     print("Please ensure the 'app' directory and 'app/__init__.py' exist and define the create_app function.")
     # Exit or raise the error depending on desired behavior during setup
     raise

# Create the application instance using the factory
# This allows for different configurations (e.g., testing) if needed later
# We pass the configuration object from config.py
# from config import Config # Assuming Config class is defined in config.py
# flask_app = create_app(Config)
# Simpler approach if create_app handles config loading internally:
flask_app = create_app()


if __name__ == '__main__':
    # Run the Flask development server
    # debug=True enables auto-reloading and detailed error pages (DO NOT use in production)
    # host='0.0.0.0' makes the server accessible on your local network
    print("Starting Flask development server...")
    # Port can be configured via environment variable or default to 5000
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=True)

