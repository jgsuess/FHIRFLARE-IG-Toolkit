# app/auth/routes.py
from flask import render_template, flash, redirect, url_for, request
from flask_login import current_user, login_user, logout_user # Keep current_user for checking auth status
from app import db
from app.models import User
from app.auth import bp # Import the auth blueprint
from .forms import LoginForm # Import LoginForm from within auth blueprint

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Redirect authenticated users away from login page
        # Maybe check role here too? Or just send to core index.
        if current_user.role == 'admin':
             return redirect(url_for('control_panel.index'))
        else:
             return redirect(url_for('core.index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password', 'danger')
            return redirect(url_for('auth.login'))

        # Log the user in
        login_user(user, remember=form.remember_me.data)
        flash(f'Welcome back, {user.username}!', 'success')

        # --- Redirect Logic Modified ---
        next_page = request.args.get('next')

        # IMPORTANT: Validate next_page to prevent Open Redirect attacks
        # Ensure it's a relative path within our site
        if next_page and not next_page.startswith('/'):
             flash('Invalid redirect specified.', 'warning') # Optional feedback
             next_page = None # Discard invalid or external URLs

        # If no valid 'next' page was provided, determine default based on role
        if not next_page:
            if user.role == 'admin':
                # Default redirect for admins
                next_page = url_for('control_panel.index')
            else:
                # Default redirect for non-admins (e.g., 'user' role)
                next_page = url_for('core.index')
        # --- End of Modified Redirect Logic ---

        return redirect(next_page)

    # Render login template (GET request or failed POST validation)
    # Assuming template is directly in blueprint's template folder
    return render_template('login.html', title='Sign In', form=form)

@bp.route('/logout')
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('core.index'))