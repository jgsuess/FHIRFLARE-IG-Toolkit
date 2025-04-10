# tests/test_auth.py

import pytest
from flask import url_for, session, request # Keep request import
from app.models import User
from app import db
from urllib.parse import urlparse, parse_qs # Keep URL parsing tools

# --- Helper to create a user ---
# (Using the version that defaults email based on username)
def create_test_user(username="testuser", email=None, password="password", role="user"):
    """Helper function to add a user to the test database."""
    if email is None:
        email = f"{username}@example.test" # Default email based on username
    # Check if user already exists by username or email
    user = User.query.filter((User.username == username) | (User.email == email)).first()
    if user:
        print(f"\nDEBUG: Found existing test user '{user.username}' with ID {user.id}")
        return user
    user = User(username=username, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    print(f"\nDEBUG: Created test user '{username}' (Role: {role}) with ID {user.id}")
    return user

# --- Tests ---

def test_login_page_loads(client):
    """Test that the login page loads correctly."""
    response = client.get(url_for('auth.login'))
    assert response.status_code == 200
    assert b"Login" in response.data
    assert b"Username" in response.data
    assert b"Password" in response.data

def test_successful_login_as_admin(client, app):
    """Test logging in with correct ADMIN credentials."""
    with app.app_context():
        admin_user = create_test_user(
            username="test_admin",
            email="admin@example.test",
            password="password",
            role="admin"
        )
        assert admin_user.id is not None
        assert admin_user.role == 'admin'

    response = client.post(url_for('auth.login'), data={
        'username': 'test_admin',
        'password': 'password'
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b"Logout" in response.data
    assert bytes(f"{admin_user.username}", 'utf-8') in response.data
    assert b"Control Panel" in response.data
    # --- CORRECTED ASSERTION ---
    assert b"Manage Modules" in response.data # Check for the button/link text


def test_login_wrong_password(client, app):
    """Test logging in with incorrect password."""
    with app.app_context():
        create_test_user(username="wrong_pass_user", password="password")
    response = client.post(url_for('auth.login'), data={
        'username': 'wrong_pass_user',
        'password': 'wrongpassword'
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data
    assert b"Logout" not in response.data

def test_login_wrong_username(client):
    """Test logging in with non-existent username."""
    response = client.post(url_for('auth.login'), data={
        'username': 'nosuchuser',
        'password': 'password'
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data
    assert b"Logout" not in response.data

def test_successful_login_as_user(client, app):
    """Test logging in with correct USER credentials."""
    with app.app_context():
        test_user = create_test_user(
            username="test_user",
            email="user@example.test",
            password="password",
            role="user"
        )
        assert test_user.id is not None
        assert test_user.role == 'user'
    response = client.post(url_for('auth.login'), data={
        'username': 'test_user',
        'password': 'password'
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Logout" in response.data
    assert bytes(f"{test_user.username}", 'utf-8') in response.data
    assert b"Control Panel" not in response.data
    site_name = app.config.get('SITE_NAME', 'PAS Framework')
    assert bytes(site_name, 'utf-8') in response.data


# --- Replace the existing test_logout function with this: ---
def test_logout(client, app):
    """Test logging out."""
    with app.app_context():
        user = create_test_user(username='logout_user', password='password')
    login_res = client.post(url_for('auth.login'), data={'username': 'logout_user', 'password': 'password'})
    assert login_res.status_code == 302

    logout_response = client.get(url_for('auth.logout'), follow_redirects=True)
    assert logout_response.status_code == 200
    assert b"You have been logged out." in logout_response.data
    assert b"Login" in logout_response.data
    assert b"Logout" not in logout_response.data

    # Assert: Accessing protected page redirects to login
    protected_response = client.get(url_for('control_panel.index'), follow_redirects=False)
    assert protected_response.status_code == 302

    # --- Use Manual Path Comparison ---
    redirect_location = protected_response.headers.get('Location', '')
    parsed_location = urlparse(redirect_location)
    query_params = parse_qs(parsed_location.query)

    # Manually define the expected RELATIVE paths
    expected_login_path_manual = '/auth/login'
    expected_next_path_manual = '/control-panel/' # Includes trailing slash from previous logs

    # Compare the path from the header with the known relative string
    assert parsed_location.path == expected_login_path_manual

    # Check the 'next' parameter
    assert 'next' in query_params
    assert query_params['next'][0] == expected_next_path_manual


# --- Replace the existing test_login_required_redirect function with this: ---
def test_login_required_redirect(client, app):
    """Test that accessing a protected page redirects to login when logged out."""
    # Act: Attempt to access control panel index
    response = client.get(url_for('control_panel.index'), follow_redirects=False)

    # Assert: Check for redirect status code (302)
    assert response.status_code == 302

    # --- Use Manual Path Comparison ---
    redirect_location = response.headers.get('Location', '')
    parsed_location = urlparse(redirect_location)
    query_params = parse_qs(parsed_location.query)

    # Manually define the expected RELATIVE paths
    expected_login_path_manual = '/auth/login'
    expected_next_path_manual = '/control-panel/' # Includes trailing slash

    # Compare the path from the header with the known relative string
    assert parsed_location.path == expected_login_path_manual

    # Check the 'next' query parameter exists and has the correct value
    assert 'next' in query_params
    assert query_params['next'][0] == expected_next_path_manual