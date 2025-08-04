import unittest
import os
import sys
import json
import tarfile
import shutil
import io
import requests
import time
import subprocess
from unittest.mock import patch, MagicMock, mock_open, call
from flask import Flask, session
from flask.testing import FlaskClient
from datetime import datetime, timezone

# Add the parent directory (/app) to sys.path
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app import app, db, ProcessedIg
import services

# Helper function to parse NDJSON stream
def parse_ndjson(byte_stream):
    decoded_stream = byte_stream.decode('utf-8').strip()
    if not decoded_stream:
        return []
    lines = decoded_stream.split('\n')
    return [json.loads(line) for line in lines if line.strip()]

class DockerComposeContainer:
    """
    A class that follows the Testcontainers pattern for managing Docker Compose environments.
    This implementation uses subprocess to call docker-compose directly since we're not 
    installing the testcontainers-python package.
    """
    
    def __init__(self, compose_file_path):
        """
        Initialize with the path to the docker-compose.yml file
        
        Args:
            compose_file_path: Path to the docker-compose.yml file
        """
        self.compose_file = compose_file_path
        self.compose_dir = os.path.dirname(os.path.abspath(compose_file_path))
        self.containers_up = False
        self.service_ports = {}
        self._container_ids = {}
    
    def __enter__(self):
        """Start containers when entering context"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop containers when exiting context"""
        self.stop()
    
    def with_service_port(self, service_name, port):
        """
        Map a service port (following the testcontainers builder pattern)
        
        Args:
            service_name: Name of the service in docker-compose.yml
            port: Port number to expose
            
        Returns:
            self for chaining
        """
        self.service_ports[service_name] = port
        return self
    
    def start(self):
        """Start the Docker Compose environment"""
        if self.containers_up:
            return self
            
        print("Starting Docker Compose environment...")
        result = subprocess.run(
            ['docker-compose', '-f', self.compose_file, 'up', '-d'],
            cwd=self.compose_dir,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            error_msg = f"Failed to start Docker Compose environment: {result.stderr}"
            print(error_msg)
            raise RuntimeError(error_msg)
        
        # Store container IDs for later use
        self._get_container_ids()
        
        self.containers_up = True
        self._wait_for_services()
        return self
    
    def _get_container_ids(self):
        """Get the container IDs for all services"""
        result = subprocess.run(
            ['docker-compose', '-f', self.compose_file, 'ps', '-q'],
            cwd=self.compose_dir,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            return
            
        container_ids = result.stdout.strip().split('\n')
        if not container_ids:
            return
            
        # Get service names for each container
        for container_id in container_ids:
            if not container_id:
                continue
                
            inspect_result = subprocess.run(
                ['docker', 'inspect', '--format', '{{index .Config.Labels "com.docker.compose.service"}}', container_id],
                capture_output=True,
                text=True
            )
            
            if inspect_result.returncode == 0:
                service_name = inspect_result.stdout.strip()
                self._container_ids[service_name] = container_id
    
    def get_container_id(self, service_name):
        """
        Get the container ID for a specific service
        
        Args:
            service_name: Name of the service in docker-compose.yml
            
        Returns:
            Container ID as string or None if not found
        """
        return self._container_ids.get(service_name)
    
    def get_service_host(self, service_name):
        """
        Get the host for a specific service - for Docker Compose we just use localhost
        
        Args:
            service_name: Name of the service in docker-compose.yml
            
        Returns:
            Host as string (usually localhost)
        """
        return "localhost"
    
    def get_service_url(self, service_name, path=""):
        """
        Get the URL for a specific service
        
        Args:
            service_name: Name of the service in docker-compose.yml
            path: Optional path to append to the URL
            
        Returns:
            URL as string
        """
        port = self.service_ports.get(service_name)
        if not port:
            raise ValueError(f"No port mapping defined for service {service_name}")
            
        url = f"http://{self.get_service_host(service_name)}:{port}"
        if path:
            # Ensure path starts with /
            if not path.startswith('/'):
                path = f"/{path}"
            url = f"{url}{path}"
            
        return url
    
    def get_logs(self, service_name):
        """
        Get logs for a specific service
        
        Args:
            service_name: Name of the service in docker-compose.yml
            
        Returns:
            Logs as string
        """
        container_id = self.get_container_id(service_name)
        if not container_id:
            return f"No container found for service {service_name}"
            
        result = subprocess.run(
            ['docker', 'logs', container_id],
            capture_output=True,
            text=True
        )
        
        return result.stdout
    
    def stop(self):
        """Stop the Docker Compose environment"""
        if not self.containers_up:
            return
            
        print("Stopping Docker Compose environment...")
        result = subprocess.run(
            ['docker-compose', '-f', self.compose_file, 'down'],
            cwd=self.compose_dir,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            print(f"Warning: Error stopping Docker Compose: {result.stderr}")
        
        self.containers_up = False
    
    def _wait_for_services(self):
        """Wait for all services to be ready"""
        print("Waiting for services to be ready...")
        
        # Wait for HAPI FHIR server
        if 'fhir' in self.service_ports:
            self._wait_for_http_service(
                self.get_service_url('fhir', 'fhir/metadata'),
                "HAPI FHIR server"
            )
        
        # Wait for FHIRFLARE application
        if 'fhirflare' in self.service_ports:
            self._wait_for_http_service(
                self.get_service_url('fhirflare'),
                "FHIRFLARE application"
            )
        
        # Give additional time for services to stabilize
        time.sleep(5)
    
    def _wait_for_http_service(self, url, service_name, max_retries=30, retry_interval=2):
        """
        Wait for an HTTP service to be ready
        
        Args:
            url: URL to check
            service_name: Name of the service for logging
            max_retries: Maximum number of retries
            retry_interval: Interval between retries in seconds
        """
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    print(f"{service_name} is ready after {attempt + 1} attempts")
                    return True
            except requests.RequestException:
                pass
            
            print(f"Waiting for {service_name} (attempt {attempt + 1}/{max_retries})...")
            time.sleep(retry_interval)
        
        print(f"Warning: {service_name} did not become ready in time")
        return False

class TestFHIRFlareIGToolkit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Define the Docker Compose container
        compose_file_path = os.path.join(os.path.dirname(__file__), 'docker-compose.yml')
        cls.container = DockerComposeContainer(compose_file_path) \
            .with_service_port('fhir', 8080) \
            .with_service_port('fhirflare', 5000)
        
        # Start the containers
        cls.container.start()
        
        # Configure app for testing
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        cls.test_packages_dir = os.path.join(os.path.dirname(__file__), 'test_fhir_packages_temp')
        app.config['FHIR_PACKAGES_DIR'] = cls.test_packages_dir
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['API_KEY'] = 'test-api-key'
        app.config['VALIDATE_IMPOSED_PROFILES'] = True
        app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = True
        app.config['HAPI_FHIR_URL'] = cls.container.get_service_url('fhir', 'fhir')  # Point to containerized HAPI FHIR

        cls.app_context = app.app_context()
        cls.app_context.push()
        db.create_all()
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()
        if os.path.exists(cls.test_packages_dir):
            shutil.rmtree(cls.test_packages_dir)
        
        # Stop Docker Compose environment
        cls.container.stop()

    def setUp(self):
        if os.path.exists(self.test_packages_dir):
            shutil.rmtree(self.test_packages_dir)
        os.makedirs(self.test_packages_dir, exist_ok=True)
        with self.app_context:
            for item in db.session.query(ProcessedIg).all():
                db.session.delete(item)
            db.session.commit()

    def tearDown(self):
        pass

    # Helper Method
    def create_mock_tgz(self, filename, files_content):
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        with tarfile.open(tgz_path, "w:gz") as tar:
            for name, content in files_content.items():
                if isinstance(content, (dict, list)):
                    data_bytes = json.dumps(content).encode('utf-8')
                elif isinstance(content, str):
                    data_bytes = content.encode('utf-8')
                else:
                    raise TypeError(f"Unsupported type for mock file '{name}': {type(content)}")
                file_io = io.BytesIO(data_bytes)
                tarinfo = tarfile.TarInfo(name=name)
                tarinfo.size = len(data_bytes)
                tarinfo.mtime = int(datetime.now(timezone.utc).timestamp())
                tar.addfile(tarinfo, file_io)
        return tgz_path

    # --- Phase 1 Tests ---

    def test_01_navigate_fhir_path(self):
        resource = {
            "resourceType": "Patient",
            "name": [{"given": ["John"]}],
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-ssn", "sliceName": "us-ssn"}],
            "extension": [{"url": "http://hl7.org/fhir/StructureDefinition/patient-birthPlace", "valueAddress": {"city": "Boston"}}]
        }
        self.assertEqual(services.navigate_fhir_path(resource, "Patient.name[0].given"), ["John"])
        self.assertEqual(services.navigate_fhir_path(resource, "Patient.identifier:us-ssn.system"), "http://hl7.org/fhir/sid/us-ssn")
        self.assertEqual(services.navigate_fhir_path(resource, "Patient.extension", extension_url="http://hl7.org/fhir/StructureDefinition/patient-birthPlace")["valueAddress"]["city"], "Boston")
        with patch('fhirpath.evaluate', side_effect=Exception("fhirpath error")):
            self.assertEqual(services.navigate_fhir_path(resource, "Patient.name[0].given"), ["John"])

    # --- Basic Page Rendering Tests ---

    def test_03_homepage(self):
        # Connect to the containerized application
        response = requests.get(self.container.get_service_url('fhirflare'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('FHIRFLARE IG Toolkit', response.text)

    def test_04_import_ig_page(self):
        response = requests.get(self.container.get_service_url('fhirflare', 'import-ig'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Import IG', response.text)
        self.assertIn('Package Name', response.text)
        self.assertIn('Package Version', response.text)
        self.assertIn('name="dependency_mode"', response.text)

    # --- API Integration Tests ---

    def test_30_load_ig_to_hapi_integration(self):
        """Test loading an IG to the containerized HAPI FHIR server"""
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        self.create_mock_tgz(filename, {
            'package/package.json': {'name': pkg_name, 'version': pkg_version},
            'package/StructureDefinition-us-core-patient.json': {
                'resourceType': 'StructureDefinition',
                'id': 'us-core-patient',
                'url': 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient',
                'name': 'USCorePatientProfile',
                'type': 'Patient',
                'status': 'active'
            }
        })
        
        # Load IG to HAPI
        response = self.client.post(
            '/api/load-ig-to-hapi',
            data=json.dumps({'package_name': pkg_name, 'version': pkg_version}),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key'}
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'success')
        
        # Verify the resource was loaded by querying the HAPI FHIR server directly
        hapi_response = requests.get(self.container.get_service_url('fhir', 'fhir/StructureDefinition/us-core-patient'))
        self.assertEqual(hapi_response.status_code, 200)
        resource = hapi_response.json()
        self.assertEqual(resource['resourceType'], 'StructureDefinition')
        self.assertEqual(resource['id'], 'us-core-patient')

    def test_31_validate_sample_with_hapi_integration(self):
        """Test validating a sample against the containerized HAPI FHIR server"""
        # First, load the necessary StructureDefinition
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        self.create_mock_tgz(filename, {
            'package/package.json': {'name': pkg_name, 'version': pkg_version},
            'package/StructureDefinition-us-core-patient.json': {
                'resourceType': 'StructureDefinition',
                'id': 'us-core-patient',
                'url': 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient',
                'name': 'USCorePatientProfile',
                'type': 'Patient',
                'status': 'active',
                'snapshot': {
                    'element': [
                        {'path': 'Patient', 'min': 1, 'max': '1'},
                        {'path': 'Patient.name', 'min': 1, 'max': '*'},
                        {'path': 'Patient.identifier', 'min': 0, 'max': '*', 'mustSupport': True}
                    ]
                }
            }
        })
        
        # Load IG to HAPI
        self.client.post(
            '/api/load-ig-to-hapi',
            data=json.dumps({'package_name': pkg_name, 'version': pkg_version}),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key'}
        )
        
        # Validate a sample that's missing a required element
        sample_resource = {
            'resourceType': 'Patient',
            'id': 'test-patient',
            'meta': {'profile': ['http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient']}
            # Missing required 'name' element
        }
        
        response = self.client.post(
            '/api/validate-sample',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'sample_data': json.dumps(sample_resource),
                'mode': 'single',
                'include_dependencies': True
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key'}
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data['valid'])
        # Check for validation error related to missing name
        found_name_error = any('name' in error for error in data['errors'])
        self.assertTrue(found_name_error, f"Expected error about missing name element, got: {data['errors']}")

    def test_32_push_ig_to_hapi_integration(self):
        """Test pushing multiple resources from an IG to the containerized HAPI FHIR server"""
        pkg_name = 'test.push.pkg'
        pkg_version = '1.0.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        
        # Create a test package with multiple resources
        self.create_mock_tgz(filename, {
            'package/package.json': {'name': pkg_name, 'version': pkg_version},
            'package/Patient-test1.json': {
                'resourceType': 'Patient',
                'id': 'test1',
                'name': [{'family': 'Test', 'given': ['Patient']}]
            },
            'package/Observation-test1.json': {
                'resourceType': 'Observation',
                'id': 'test1',
                'status': 'final',
                'code': {'coding': [{'system': 'http://loinc.org', 'code': '12345-6'}]}
            }
        })
        
        # Push the IG to HAPI
        response = self.client.post(
            '/api/push-ig',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'fhir_server_url': self.container.get_service_url('fhir', 'fhir'),
                'include_dependencies': False
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key', 'Accept': 'application/x-ndjson'}
        )
        
        self.assertEqual(response.status_code, 200)
        streamed_data = parse_ndjson(response.data)
        complete_msg = next((item for item in streamed_data if item.get('type') == 'complete'), None)
        self.assertIsNotNone(complete_msg, "Complete message not found in streamed response")
        summary = complete_msg.get('data', {})
        self.assertTrue(summary.get('success_count') >= 2, f"Expected at least 2 successful resources, got {summary.get('success_count')}")
        
        # Verify resources were loaded by querying the HAPI FHIR server directly
        patient_response = requests.get(self.container.get_service_url('fhir', 'fhir/Patient/test1'))
        self.assertEqual(patient_response.status_code, 200)
        patient = patient_response.json()
        self.assertEqual(patient['resourceType'], 'Patient')
        self.assertEqual(patient['id'], 'test1')
        
        observation_response = requests.get(self.container.get_service_url('fhir', 'fhir/Observation/test1'))
        self.assertEqual(observation_response.status_code, 200)
        observation = observation_response.json()
        self.assertEqual(observation['resourceType'], 'Observation')
        self.assertEqual(observation['id'], 'test1')

    # --- Existing API Tests ---

    @patch('app.list_downloaded_packages')
    @patch('app.services.process_package_file')
    @patch('app.services.import_package_and_dependencies')
    @patch('os.path.exists')
    def test_40_api_import_ig_success(self, mock_os_exists, mock_import, mock_process, mock_list_pkgs):
        pkg_name = 'api.test.pkg'
        pkg_version = '1.2.3'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        pkg_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        mock_import.return_value = {'requested': (pkg_name, pkg_version), 'processed': {(pkg_name, pkg_version)}, 'downloaded': {(pkg_name, pkg_version): pkg_path}, 'all_dependencies': {}, 'dependencies': [], 'errors': []}
        mock_process.return_value = {'resource_types_info': [], 'must_support_elements': {}, 'examples': {}, 'complies_with_profiles': ['http://prof.com/a'], 'imposed_profiles': [], 'errors': []}
        mock_os_exists.return_value = True
        mock_list_pkgs.return_value = ([{'name': pkg_name, 'version': pkg_version, 'filename': filename}], [], {})
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': pkg_name, 'version': pkg_version, 'dependency_mode': 'direct', 'api_key': 'test-api-key'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['complies_with_profiles'], ['http://prof.com/a'])

    @patch('app.services.import_package_and_dependencies')
    def test_41_api_import_ig_failure(self, mock_import):
        mock_import.return_value = {'requested': ('bad.pkg', '1.0'), 'processed': set(), 'downloaded': {}, 'all_dependencies': {}, 'dependencies': [], 'errors': ['HTTP error: 404 Not Found']}
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': 'bad.pkg', 'version': '1.0', 'api_key': 'test-api-key'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertIn('Failed to import bad.pkg#1.0: HTTP error: 404 Not Found', data['message'])

    def test_42_api_import_ig_invalid_key(self):
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': 'a', 'version': '1', 'api_key': 'wrong'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)

    def test_43_api_import_ig_missing_key(self):
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': 'a', 'version': '1'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)

    # --- API Push Tests ---

    @patch('os.path.exists', return_value=True)
    @patch('app.services.get_package_metadata')
    @patch('tarfile.open')
    @patch('requests.Session')
    def test_50_api_push_ig_success(self, mock_session, mock_tarfile_open, mock_get_metadata, mock_os_exists):
        pkg_name = 'push.test.pkg'
        pkg_version = '1.0.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        fhir_server_url = self.container.get_service_url('fhir', 'fhir')
        mock_get_metadata.return_value = {'imported_dependencies': []}
        mock_tar = MagicMock()
        mock_patient = {'resourceType': 'Patient', 'id': 'pat1'}
        mock_obs = {'resourceType': 'Observation', 'id': 'obs1', 'status': 'final'}
        patient_member = MagicMock(spec=tarfile.TarInfo)
        patient_member.name = 'package/Patient-pat1.json'
        patient_member.isfile.return_value = True
        obs_member = MagicMock(spec=tarfile.TarInfo)
        obs_member.name = 'package/Observation-obs1.json'
        obs_member.isfile.return_value = True
        mock_tar.getmembers.return_value = [patient_member, obs_member]
        def mock_extractfile(member):
            if member.name == 'package/Patient-pat1.json':
                return io.BytesIO(json.dumps(mock_patient).encode('utf-8'))
            if member.name == 'package/Observation-obs1.json':
                return io.BytesIO(json.dumps(mock_obs).encode('utf-8'))
            return None
        mock_tar.extractfile.side_effect = mock_extractfile
        mock_tarfile_open.return_value.__enter__.return_value = mock_tar
        mock_session_instance = MagicMock()
        mock_put_response = MagicMock(status_code=200)
        mock_put_response.raise_for_status.return_value = None
        mock_session_instance.put.return_value = mock_put_response
        mock_session.return_value = mock_session_instance
        self.create_mock_tgz(filename, {'package/dummy.txt': 'content'})
        response = self.client.post(
            '/api/push-ig',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'fhir_server_url': fhir_server_url,
                'include_dependencies': False,
                'api_key': 'test-api-key'
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key', 'Accept': 'application/x-ndjson'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/x-ndjson')
        streamed_data = parse_ndjson(response.data)
        complete_msg = next((item for item in streamed_data if item.get('type') == 'complete'), None)
        self.assertIsNotNone(complete_msg)
        summary = complete_msg.get('data', {})
        self.assertEqual(summary.get('status'), 'success')
        self.assertEqual(summary.get('success_count'), 2)
        self.assertEqual(len(summary.get('failed_details')), 0)
        mock_os_exists.assert_called_with(os.path.join(self.test_packages_dir, filename))

    # --- Helper method to debug container issues ---
    
    def test_99_print_container_logs_on_failure(self):
        """Helper test that prints container logs in case of failures"""
        # This test should always pass but will print logs if other tests fail
        try:
            if hasattr(self, 'container') and self.container.containers_up:
                for service_name in ['fhir', 'db', 'fhirflare']:
                    if service_name in self.container._container_ids:
                        print(f"\n=== Logs for {service_name} ===")
                        print(self.container.get_logs(service_name))
        except Exception as e:
            print(f"Error getting container logs: {e}")
        
        # This assertion always passes - this test is just for debug info
        self.assertTrue(True)
        
if __name__ == '__main__':
    unittest.main()