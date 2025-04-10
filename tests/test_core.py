# tests/test_core.py

from flask import url_for

def test_app_exists(app):
    """ Test if the Flask app fixture loads correctly. """
    assert app is not None

def test_request_index_page(client, app):
    """
    Test if the index page loads successfully (GET request).
    Uses the 'client' fixture provided by conftest.py.
    """
    # Make a GET request to the root URL ('/')
    # Note: We use '/' here, assuming your core blueprint maps '/' or '/index'
    response = client.get('/')

    # Assert that the HTTP status code is 200 (OK)
    assert response.status_code == 200

    # Optional: Assert that some expected content is in the response HTML
    # We access response.data, which is bytes, hence the b"..." prefix
    # Let's check for the site name defined in config.py
    site_name = app.config.get('SITE_NAME', 'PAS Framework') # Get site name from app config
    assert bytes(site_name, 'utf-8') in response.data

    # Optional: Test using url_for within the test context
    # This requires the app context from the fixture
    # Need to ensure SERVER_NAME is set in TestingConfig if using external=True
    # response_index = client.get(url_for('core.index'))
    # assert response_index.status_code == 200