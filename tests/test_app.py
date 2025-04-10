import unittest
import pytest
import os
import sys
import json
import tarfile
import shutil
from unittest.mock import patch, MagicMock
from flask import Flask, url_for
from flask.testing import FlaskClient

# Add the parent directory (/app) to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db, ProcessedIg
from datetime import datetime

class TestFHIRFlareIGToolkit(unittest.TestCase):
    def setUp(self):
        # Configure the Flask app for testing
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['FHIR_PACKAGES_DIR'] = 'test_packages'
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['API_KEY'] = 'test-api-key'

        # Create the test packages directory
        os.makedirs(app.config['FHIR_PACKAGES_DIR'], exist_ok=True)

        # Create the Flask test client
        self.client = app.test_client()

        # Initialize the database
        with app.app_context():
            db.create_all()

    def tearDown(self):
        # Clean up the database and test packages directory
        with app.app_context():
            db.session.remove()
            db.drop_all()
        if os.path.exists(app.config['FHIR_PACKAGES_DIR']):
            shutil.rmtree(app.config['FHIR_PACKAGES_DIR'])

    # Helper method to create a mock .tgz file
    def create_mock_tgz(self, filename, content=None):
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        with tarfile.open(tgz_path, "w:gz") as tar:
            if content:
                # Create a mock package.json file inside the .tgz
                import io
                package_json = io.BytesIO(json.dumps(content).encode('utf-8'))
                tarinfo = tarfile.TarInfo(name="package/package.json")
                tarinfo.size = len(package_json.getvalue())
                tar.addfile(tarinfo, package_json)
        return tgz_path

    # Test Case 1: Test Homepage Rendering
    def test_homepage(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'FHIRFLARE IG Toolkit', response.data)

    # Test Case 2: Test Import IG Page Rendering
    def test_import_ig_page(self):
        response = self.client.get('/import-ig')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Import IG', response.data)
        self.assertIn(b'Package Name', response.data)
        self.assertIn(b'Package Version', response.data)

    # Test Case 3: Test Import IG Form Submission (Success)
    @patch('services.import_package_and_dependencies')
    def test_import_ig_success(self, mock_import):
        mock_import.return_value = {
            'downloaded': True,
            'errors': [],
            'dependencies': [{'name': 'hl7.fhir.r4.core', 'version': '4.0.1'}]
        }
        response = self.client.post('/import-ig', data={
            'package_name': 'hl7.fhir.us.core',
            'package_version': '3.1.1'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Successfully downloaded hl7.fhir.us.core#3.1.1', response.data)

    # Test Case 4: Test Import IG Form Submission (Failure)
    @patch('services.import_package_and_dependencies')
    def test_import_ig_failure(self, mock_import):
        mock_import.return_value = {
            'downloaded': False,
            'errors': ['Package not found'],
            'dependencies': []
        }
        response = self.client.post('/import-ig', data={
            'package_name': 'invalid.package',
            'package_version': '1.0.0'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Failed to import invalid.package#1.0.0', response.data)

    # Test Case 5: Test Import IG Form Submission (Invalid Input)
    def test_import_ig_invalid_input(self):
        response = self.client.post('/import-ig', data={
            'package_name': 'invalid@package',  # Invalid format
            'package_version': '1.0.0'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid package name format', response.data)

    # Test Case 6: Test View IGs Page Rendering (No Packages)
    def test_view_igs_no_packages(self):
        response = self.client.get('/view-igs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'No packages downloaded yet', response.data)

    # Test Case 7: Test View IGs Page Rendering (With Packages)
    def test_view_igs_with_packages(self):
        # Create a mock .tgz file
        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz', {
            'name': 'hl7.fhir.us.core',
            'version': '3.1.1'
        })
        response = self.client.get('/view-igs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'hl7.fhir.us.core', response.data)
        self.assertIn(b'3.1.1', response.data)

    # Test Case 8: Test Process IG (Success)
    @patch('services.process_package_file')
    def test_process_ig_success(self, mock_process):
        mock_process.return_value = {
            'resource_types_info': [{'type': 'Patient'}],
            'must_support_elements': {'Patient': ['name']},
            'examples': {'Patient': ['example1.json']}
        }
        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz')
        response = self.client.post('/process-igs', data={
            'filename': 'hl7.fhir.us.core-3.1.1.tgz'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Successfully processed hl7.fhir.us.core#3.1.1', response.data)

    # Test Case 9: Test Process IG (Invalid File)
    def test_process_ig_invalid_file(self):
        response = self.client.post('/process-igs', data={
            'filename': 'invalid-file.txt'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid package file', response.data)

    # Test Case 10: Test Delete IG (Success)
    def test_delete_ig_success(self):
        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz')
        response = self.client.post('/delete-ig', data={
            'filename': 'hl7.fhir.us.core-3.1.1.tgz'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Deleted hl7.fhir.us.core-3.1.1.tgz', response.data)
        self.assertFalse(os.path.exists(os.path.join(app.config['FHIR_PACKAGES_DIR'], 'hl7.fhir.us.core-3.1.1.tgz')))

    # Test Case 11: Test Delete IG (File Not Found)
    def test_delete_ig_file_not_found(self):
        response = self.client.post('/delete-ig', data={
            'filename': 'nonexistent.tgz'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'File not found: nonexistent.tgz', response.data)

    # Test Case 12: Test Unload IG (Success)
    def test_unload_ig_success(self):
        with app.app_context():
            processed_ig = ProcessedIg(
                package_name='hl7.fhir.us.core',
                version='3.1.1',
                processed_date=datetime.now(),
                resource_types_info=[{'type': 'Patient'}]
            )
            db.session.add(processed_ig)
            db.session.commit()
            ig_id = processed_ig.id

        response = self.client.post('/unload-ig', data={
            'ig_id': ig_id
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Unloaded hl7.fhir.us.core#3.1.1', response.data)

    # Test Case 13: Test Unload IG (Invalid ID)
    def test_unload_ig_invalid_id(self):
        response = self.client.post('/unload-ig', data={
            'ig_id': '999'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Package not found with ID: 999', response.data)

    # Test Case 14: Test View Processed IG Page
    def test_view_processed_ig(self):
        with app.app_context():
            processed_ig = ProcessedIg(
                package_name='hl7.fhir.us.core',
                version='3.1.1',
                processed_date=datetime.now(),
                resource_types_info=[{'type': 'Patient', 'is_profile': False}]
            )
            db.session.add(processed_ig)
            db.session.commit()
            ig_id = processed_ig.id

        response = self.client.get(f'/view-ig/{ig_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'View hl7.fhir.us.core#3.1.1', response.data)

    # Test Case 15: Test Push IGs Page Rendering
    def test_push_igs_page(self):
        response = self.client.get('/push-igs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Push IGs to FHIR Server', response.data)
        self.assertIn(b'Live Console', response.data)

    # Test Case 16: Test API - Import IG (Success)
    @patch('services.import_package_and_dependencies')
    def test_api_import_ig_success(self, mock_import):
        mock_import.return_value = {
            'downloaded': True,
            'errors': [],
            'dependencies': [{'name': 'hl7.fhir.r4.core', 'version': '4.0.1'}]
        }
        response = self.client.post('/api/import-ig', 
            data=json.dumps({
                'package_name': 'hl7.fhir.us.core',
                'version': '3.1.1',
                'api_key': 'test-api-key'
            }),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['package_name'], 'hl7.fhir.us.core')

    # Test Case 17: Test API - Import IG (Invalid API Key)
    def test_api_import_ig_invalid_api_key(self):
        response = self.client.post('/api/import-ig', 
            data=json.dumps({
                'package_name': 'hl7.fhir.us.core',
                'version': '3.1.1',
                'api_key': 'wrong-api-key'
            }),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertEqual(data['message'], 'Invalid API key')

    # Test Case 18: Test API - Import IG (Missing Parameters)
    def test_api_import_ig_missing_params(self):
        response = self.client.post('/api/import-ig', 
            data=json.dumps({
                'package_name': 'hl7.fhir.us.core',
                'api_key': 'test-api-key'
            }),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertEqual(data['message'], 'Missing package_name or version')

    # Test Case 19: Test API - Push IG (Success)
    @patch('requests.put')
    def test_api_push_ig_success(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_put.return_value = mock_response

        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz', {
            'name': 'hl7.fhir.us.core',
            'version': '3.1.1'
        })
        # Add a mock resource file
        with tarfile.open(os.path.join(app.config['FHIR_PACKAGES_DIR'], 'hl7.fhir.us.core-3.1.1.tgz'), "a:gz") as tar:
            resource_data = json.dumps({
                'resourceType': 'Patient',
                'id': 'example'
            }).encode('utf-8')
            import io
            resource_file = io.BytesIO(resource_data)
            tarinfo = tarfile.TarInfo(name="package/Patient-example.json")
            tarinfo.size = len(resource_data)
            tar.addfile(tarinfo, resource_file)

        response = self.client.post('/api/push-ig',
            data=json.dumps({
                'package_name': 'hl7.fhir.us.core',
                'version': '3.1.1',
                'fhir_server_url': 'http://test-server/fhir',
                'include_dependencies': False,
                'api_key': 'test-api-key'
            }),
            content_type='application/json',
            headers={'Accept': 'application/x-ndjson'}
        )
        self.assertEqual(response.status_code, 200)
        response_text = response.data.decode('utf-8')
        self.assertIn('"type": "start"', response_text)
        self.assertIn('"type": "success"', response_text)
        self.assertIn('"status": "success"', response_text)

    # Test Case 20: Test API - Push IG (Invalid API Key)
    def test_api_push_ig_invalid_api_key(self):
        response = self.client.post('/api/push-ig',
            data=json.dumps({
                'package_name': 'hl7.fhir.us.core',
                'version': '3.1.1',
                'fhir_server_url': 'http://test-server/fhir',
                'include_dependencies': False,
                'api_key': 'wrong-api-key'
            }),
            content_type='application/json',
            headers={'Accept': 'application/x-ndjson'}
        )
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertEqual(data['message'], 'Invalid API key')

    # Test Case 21: Test API - Push IG (Package Not Found)
    def test_api_push_ig_package_not_found(self):
        response = self.client.post('/api/push-ig',
            data=json.dumps({
                'package_name': 'hl7.fhir.us.core',
                'version': '3.1.1',
                'fhir_server_url': 'http://test-server/fhir',
                'include_dependencies': False,
                'api_key': 'test-api-key'
            }),
            content_type='application/json',
            headers={'Accept': 'application/x-ndjson'}
        )
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertEqual(data['message'], 'Package not found: hl7.fhir.us.core#3.1.1')

    # Test Case 22: Test Secret Key - CSRF Protection
    def test_secret_key_csrf(self):
        # Re-enable CSRF for this test
        app.config['WTF_CSRF_ENABLED'] = True
        response = self.client.post('/import-ig', data={
            'package_name': 'hl7.fhir.us.core',
            'package_version': '3.1.1'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 400)  # CSRF token missing

    # Test Case 23: Test Secret Key - Flash Messages
    def test_secret_key_flash_messages(self):
        # Set a flash message
        with self.client as client:
            with client.session_transaction() as sess:
                sess['_flashes'] = [('success', 'Test message')]
            response = self.client.get('/push-igs')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Test message', response.data)

    # Test Case 24: Test Get Structure Definition (Success)
    def test_get_structure_definition_success(self):
        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz')
        with tarfile.open(os.path.join(app.config['FHIR_PACKAGES_DIR'], 'hl7.fhir.us.core-3.1.1.tgz'), "a:gz") as tar:
            sd_data = json.dumps({
                'snapshot': {'element': [{'id': 'Patient.name'}]}
            }).encode('utf-8')
            import io
            sd_file = io.BytesIO(sd_data)
            tarinfo = tarfile.TarInfo(name="package/StructureDefinition-Patient.json")
            tarinfo.size = len(sd_data)
            tar.addfile(tarinfo, sd_file)

        response = self.client.get('/get-structure?package_name=hl7.fhir.us.core&package_version=3.1.1&resource_type=Patient')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('elements', data)
        self.assertEqual(data['elements'][0]['id'], 'Patient.name')

    # Test Case 25: Test Get Structure Definition (Not Found)
    def test_get_structure_definition_not_found(self):
        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz')
        response = self.client.get('/get-structure?package_name=hl7.fhir.us.core&package_version=3.1.1&resource_type=Observation')
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertEqual(data['error'], "SD for 'Observation' not found.")

    # Test Case 26: Test Get Example Content (Success)
    def test_get_example_content_success(self):
        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz')
        with tarfile.open(os.path.join(app.config['FHIR_PACKAGES_DIR'], 'hl7.fhir.us.core-3.1.1.tgz'), "a:gz") as tar:
            example_data = json.dumps({
                'resourceType': 'Patient',
                'id': 'example'
            }).encode('utf-8')
            import io
            example_file = io.BytesIO(example_data)
            tarinfo = tarfile.TarInfo(name="package/example-Patient.json")
            tarinfo.size = len(example_data)
            tar.addfile(tarinfo, example_file)

        response = self.client.get('/get-example?package_name=hl7.fhir.us.core&package_version=3.1.1&filename=package/example-Patient.json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['resourceType'], 'Patient')

    # Test Case 27: Test Get Example Content (Invalid Path)
    def test_get_example_content_invalid_path(self):
        self.create_mock_tgz('hl7.fhir.us.core-3.1.1.tgz')
        response = self.client.get('/get-example?package_name=hl7.fhir.us.core&package_version=3.1.1&filename=invalid/example.json')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Invalid example file path.')

if __name__ == '__main__':
    unittest.main()