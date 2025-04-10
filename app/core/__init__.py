# app/core/__init__.py
# Initialize the core blueprint

from flask import Blueprint

# Create a Blueprint instance for core routes
# 'core' is the name of the blueprint
# __name__ helps Flask locate the blueprint's resources (like templates)
# template_folder='templates' specifies a blueprint-specific template folder (optional)
bp = Blueprint('core', __name__, template_folder='templates')

# Import the routes module associated with this blueprint
# This import is at the bottom to avoid circular dependencies
from . import routes # noqa: F401 E402
