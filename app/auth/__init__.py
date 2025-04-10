# app/auth/__init__.py
from flask import Blueprint

# Define the auth blueprint
bp = Blueprint('auth', __name__, template_folder='templates')

# Import routes at the bottom
from app.auth import routes