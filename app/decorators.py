# app/decorators.py
from functools import wraps
from flask_login import current_user
from flask import abort

def admin_required(func):
    """
    Decorator to ensure the user is logged in and has the 'admin' role.
    Aborts with 403 Forbidden if conditions are not met.
    """
    @wraps(func)
    def decorated_view(*args, **kwargs):
        # Check if user is logged in and has the admin role (using the property we added)
        if not current_user.is_authenticated or not current_user.is_admin:
            # If not admin, return a 403 Forbidden error
            abort(403)
        # If admin, proceed with the original route function
        return func(*args, **kwargs)
    return decorated_view