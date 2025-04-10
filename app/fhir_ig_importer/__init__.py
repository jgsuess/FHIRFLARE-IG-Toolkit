# app/modules/fhir_ig_importer/__init__.py

from flask import Blueprint

# --- Module Metadata ---
metadata = {
    'module_id': 'fhir_ig_importer', # Matches folder name
    'display_name': 'FHIR IG Importer',
    'description': 'Imports FHIR Implementation Guide packages from a registry.',
    'version': '0.1.0',
    # No main nav items, will be accessed via Control Panel
    'nav_items': []
}
# --- End Module Metadata ---

# Define Blueprint
# We'll mount this under the control panel later
bp = Blueprint(
    metadata['module_id'],
    __name__,
    template_folder='templates',
    # Define a URL prefix if mounting standalone, but we'll likely register
    # it under /control-panel via app/__init__.py later
    # url_prefix='/fhir-importer'
)

# Import routes after creating blueprint
from . import routes, forms # Import forms too