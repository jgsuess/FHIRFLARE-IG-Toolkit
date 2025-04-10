# app/core/routes.py
# Defines routes for the core part of the application (e.g., home page)

from flask import render_template
from . import bp  # Import the blueprint instance defined in __init__.py

# --- Core Routes ---

@bp.route('/')
@bp.route('/index')
def index():
    """Renders the main home page of the application."""
    # This will look for 'index.html' first in the blueprint's template folder
    # (if defined, e.g., 'app/core/templates/index.html')
    # and then fall back to the main application's template folder ('app/templates/index.html')
    return render_template('index.html', title='Home')

# Add other core routes here (e.g., about page, contact page) if needed
