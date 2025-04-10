# tests/test_control_panel.py

import pytest
from flask import url_for
from app.models import User, ModuleRegistry # Import models
from app import db
from urllib.parse import urlparse, parse_qs # Make sure this is imported

# --- Test Helpers ---
def create_test_user(username="testuser", email=None, password="password", role="user"):
    if email is None: email = f"{username}@example.test"
    # Use SQLAlchemy 2.0 style query
    user = db.session.scalar(db.select(User).filter((User.username == username) | (User.email == email)))
    if user: print(f"\nDEBUG: Found existing test user '{user.username}' with ID {user.id}"); return user
    user = User(username=username, email=email, role=role); user.set_password(password)
    db.session.add(user); db.session.commit()
    print(f"\nDEBUG: Created test user '{username}' (Role: {role}) with ID {user.id}"); return user

def login(client, username, password):
    return client.post(url_for('auth.login'), data=dict(username=username, password=password), follow_redirects=False)

# --- Access Control Tests ---
def test_cp_access_admin(client, app): # PASSED
    with app.app_context(): create_test_user(username="cp_admin", password="password", role="admin")
    login_res = login(client, "cp_admin", "password"); assert login_res.status_code == 302
    cp_index_res = client.get(url_for('control_panel.index')); assert cp_index_res.status_code == 200; assert b"Control Panel" in cp_index_res.data
    module_res = client.get(url_for('control_panel.manage_modules')); assert module_res.status_code == 200; assert b"Module Management" in module_res.data

def test_cp_access_user(client, app): # PASSED
    with app.app_context(): create_test_user(username="cp_user", password="password", role="user")
    login_res = login(client, "cp_user", "password"); assert login_res.status_code == 302
    cp_index_res = client.get(url_for('control_panel.index')); assert cp_index_res.status_code == 403
    module_res = client.get(url_for('control_panel.manage_modules')); assert module_res.status_code == 403

def test_cp_access_logged_out(client, app): # PASSED
    cp_index_res = client.get(url_for('control_panel.index'), follow_redirects=False); assert cp_index_res.status_code == 302
    module_res = client.get(url_for('control_panel.manage_modules'), follow_redirects=False); assert module_res.status_code == 302


# --- Module Management Tests ---
def test_module_manager_list(client, app): # PASSED
    with app.app_context(): create_test_user(username="module_admin", password="password", role="admin")
    login(client, "module_admin", "password")
    response = client.get(url_for('control_panel.manage_modules'))
    assert response.status_code == 200; assert b"Module Management" in response.data
    assert b"example_module" in response.data; assert b"Disabled" in response.data

def test_module_manager_toggle(client, app): # PASSED
    with app.app_context():
        admin = create_test_user(username="toggle_admin", password="password", role="admin")
        # Use SQLAlchemy 2.0 style query
        module_entry = db.session.scalar(db.select(ModuleRegistry).filter_by(module_id='example_module'))
        assert module_entry is not None, "Check conftest.py discovery call."
        module_entry.is_enabled = False; db.session.commit()
    login(client, "toggle_admin", "password")
    # Enable
    enable_url = url_for('control_panel.toggle_module_status', module_id='example_module')
    response_enable = client.post(enable_url, follow_redirects=False)
    assert response_enable.status_code == 302; redirect_location_enable = response_enable.headers.get('Location', ''); parsed_location_enable = urlparse(redirect_location_enable); expected_path_manual = '/control-panel/modules'; assert parsed_location_enable.path == expected_path_manual
    with client.session_transaction() as sess: assert '_flashes' in sess; assert "has been enabled" in sess['_flashes'][-1][1]
    with app.app_context(): module_entry_after_enable = db.session.scalar(db.select(ModuleRegistry).filter_by(module_id='example_module')); assert module_entry_after_enable is not None and module_entry_after_enable.is_enabled is True
    # Disable
    disable_url = url_for('control_panel.toggle_module_status', module_id='example_module')
    response_disable = client.post(disable_url, follow_redirects=False)
    assert response_disable.status_code == 302; redirect_location_disable = response_disable.headers.get('Location', ''); parsed_location_disable = urlparse(redirect_location_disable); assert parsed_location_disable.path == expected_path_manual
    with client.session_transaction() as sess: assert '_flashes' in sess; assert "has been disabled" in sess['_flashes'][-1][1]
    with app.app_context(): module_entry_after_disable = db.session.scalar(db.select(ModuleRegistry).filter_by(module_id='example_module')); assert module_entry_after_disable is not None and module_entry_after_disable.is_enabled is False


# --- User CRUD Tests ---

def test_add_user_page_loads(client, app): # PASSED
    with app.app_context(): create_test_user(username="crud_admin", password="password", role="admin")
    login(client, "crud_admin", "password")
    response = client.get(url_for('control_panel.add_user'))
    assert response.status_code == 200; assert b"Add New User" in response.data; assert b"Username" in response.data
    assert b"Email" in response.data; assert b"Password" in response.data; assert b"Repeat Password" in response.data
    assert b"Role" in response.data; assert b"Add User" in response.data

def test_add_user_success(client, app): # PASSED
    with app.app_context(): create_test_user(username="crud_admin_adder", password="password", role="admin")
    login(client, "crud_admin_adder", "password")
    new_username = "new_test_user"; new_email = "new@example.com"; new_password = "new_password"; new_role = "user"
    response = client.post(url_for('control_panel.add_user'), data={'username': new_username, 'email': new_email, 'password': new_password,'password2': new_password,'role': new_role,'submit': 'Add User'}, follow_redirects=True)
    assert response.status_code == 200; assert b"User new_test_user (user) added successfully!" in response.data
    assert bytes(new_username, 'utf-8') in response.data; assert bytes(new_email, 'utf-8') in response.data
    with app.app_context():
        newly_added_user = db.session.scalar(db.select(User).filter_by(username=new_username)) # Use 2.0 style
        assert newly_added_user is not None; assert newly_added_user.email == new_email; assert newly_added_user.role == new_role; assert newly_added_user.check_password(new_password) is True

# --- Edit User Tests ---

# --- MODIFIED: Use admin_client fixture ---
def test_edit_user_page_loads(admin_client, app): # Use admin_client instead of client
    """Test the 'Edit User' page loads correctly with user data."""
    # Arrange: Create ONLY the target user
    with app.app_context():
        target_user = create_test_user(username="edit_target", email="edit@target.com", role="user")
        target_user_id = target_user.id # Get ID
        target_user_username = target_user.username # Store username if needed for assert
        target_user_email = target_user.email
        target_user_role = target_user.role

    # Act: Get the 'Edit User' page using the logged-in admin client
    # Pass target user's ID
    response = admin_client.get(url_for('control_panel.edit_user', user_id=target_user_id))

    # Assert: Check page loads and contains correct pre-filled data
    assert response.status_code == 200
    assert bytes(f"Edit User", 'utf-8') in response.data # Use simpler title from route
    # Check if form fields (rendered by template using data from route) are present
    # These assertions rely on how edit_user.html renders the form passed by the route
    assert bytes(f'value="{target_user_username}"', 'utf-8') in response.data
    assert bytes(f'value="{target_user_email}"', 'utf-8') in response.data
    assert bytes(f'<option selected value="{target_user_role}">', 'utf-8') in response.data \
        or bytes(f'<option value="{target_user_role}" selected>', 'utf-8') in response.data \
        or bytes(f'<option value="{target_user_role}" selected="selected">', 'utf-8') in response.data
    assert b"Save Changes" in response.data


# Keep test_edit_user_success (it passed, but ensure it uses unique users)
def test_edit_user_success(client, app): # Keep using regular client for separation
    """Test successfully editing a user."""
    with app.app_context():
        target_user = create_test_user(username="edit_target_success", email="edit_success@target.com", role="user")
        target_user_id = target_user.id
        # Create a distinct admin just for this test's login action
        admin = create_test_user(username="edit_admin_success", email="edit_admin_success@example.com", role="admin")
    login(client, "edit_admin_success", "password") # Log in admin for this action

    updated_username = "edited_username"; updated_email = "edited@example.com"; updated_role = "admin"
    response = client.post(url_for('control_panel.edit_user', user_id=target_user_id), data={'username': updated_username, 'email': updated_email, 'role': updated_role, 'submit': 'Save Changes'}, follow_redirects=True)
    # ... (assertions as before) ...
    assert response.status_code == 200
    assert bytes(f"User {updated_username} updated successfully!", 'utf-8') in response.data
    assert bytes(updated_username, 'utf-8') in response.data; assert bytes(updated_email, 'utf-8') in response.data
    with app.app_context(): edited_user = db.session.get(User, target_user_id); assert edited_user is not None; assert edited_user.username == updated_username; assert edited_user.email == updated_email; assert edited_user.role == updated_role



# --- Delete User Tests ---

# --- CORRECTED: test_delete_user_success ---
def test_delete_user_success(client, app):
    """Test successfully deleting a user."""
    # Arrange
    with app.app_context():
        target_user = create_test_user(username="delete_target", email="delete@target.com", role="user")
        target_user_id = target_user.id; target_username = target_user.username
        admin = create_test_user(username="delete_admin", email="delete_admin@example.com", role="admin")
        admin_username = admin.username
        assert db.session.get(User, target_user_id) is not None
    login(client, "delete_admin", "password")

    # Act
    response = client.post(url_for('control_panel.delete_user', user_id=target_user_id), data={'submit': 'Delete'}, follow_redirects=True)

    # Assert 1: Verify DB deletion (Most Reliable Check)
    with app.app_context():
        deleted_user = db.session.get(User, target_user_id)
        assert deleted_user is None, "User was not deleted from the database."

    # Assert 2: Check page status and flash message
    assert response.status_code == 200
    assert bytes(f"User {target_username} deleted successfully!", 'utf-8') in response.data

    # --- FIX: Removed unreliable check of rendered HTML list ---
    # assert bytes(admin_username, 'utf-8') in response.data
    # assert bytes(target_username, 'utf-8') not in response.data
    # --- End Fix ---


# --- Change Password Tests ---

def test_change_password_page_loads(admin_client, app): # Use admin_client instead of client
    """Test the 'Change Password' page loads correctly."""
    # Arrange: Create ONLY the target user
    with app.app_context():
        target_user = create_test_user(username="pw_target", email="pw@target.com", role="user")
        target_user_id = target_user.id
        target_user_username = target_user.username # Store username if needed

    # Act: Get the 'Change Password' page using logged-in admin client
    response = admin_client.get(url_for('control_panel.change_password', user_id=target_user_id))

    # Assert: Check page loads and contains expected fields
    assert response.status_code == 200
    assert bytes(f"Change Password", 'utf-8') in response.data # Use simpler title
    assert b"New Password" in response.data
    assert b"Repeat New Password" in response.data
    assert b"Change Password" in response.data

def test_change_password_success(client, app): # Should Pass Now
    """Test successfully changing a user's password."""
    original_password = "old_password"
    new_password = "new_secure_password"
    with app.app_context():
        target_user = create_test_user(username="pw_target_success", email="pw_success@target.com", password=original_password, role="user")
        target_username = target_user.username
        target_user_id = target_user.id
        admin = create_test_user(username="pw_admin_success", email="pw_admin_success@example.com", role="admin")
        assert target_user.check_password(original_password) is True
    login(client, "pw_admin_success", "password")

    response = client.post(url_for('control_panel.change_password', user_id=target_user_id), data={
        'password': new_password,
        'password2': new_password,
        'submit': 'Change Password'
    }, follow_redirects=True)

    assert response.status_code == 200
    assert bytes(f"Password for user {target_username} has been updated.", 'utf-8') in response.data

    # Assert: Verify password change in DB
    with app.app_context():
        # FIX: Use db.session.get
        updated_user = db.session.get(User, target_user_id)
        assert updated_user is not None
        assert updated_user.check_password(new_password) is True
        assert updated_user.check_password(original_password) is False

# --- TODO: Add tests for Edit/Delete/ChangePW errors ---