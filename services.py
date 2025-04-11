# app/modules/fhir_ig_importer/services.py

import requests
import os
import tarfile
import gzip
import json
import io
import re
import logging
from flask import current_app
from collections import defaultdict

# Constants
FHIR_REGISTRY_BASE_URL = "https://packages.fhir.org"
DOWNLOAD_DIR_NAME = "fhir_packages"
CANONICAL_PACKAGE = ("hl7.fhir.r4.core", "4.0.1")  # Define the canonical FHIR package

# --- Helper Functions ---

def _get_download_dir():
    """Gets the absolute path to the download directory, creating it if needed."""
    logger = logging.getLogger(__name__)
    instance_path = None  # Initialize
    try:
        instance_path = current_app.instance_path
        logger.debug(f"Using instance path from current_app: {instance_path}")
    except RuntimeError:
        logger.warning("No app context for instance_path, constructing relative path.")
        instance_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'instance'))
        logger.debug(f"Constructed instance path: {instance_path}")

    if not instance_path:
        logger.error("Fatal Error: Could not determine instance path.")
        return None

    download_dir = os.path.join(instance_path, DOWNLOAD_DIR_NAME)
    try:
        os.makedirs(download_dir, exist_ok=True)
        return download_dir
    except OSError as e:
        logger.error(f"Fatal Error creating dir {download_dir}: {e}", exc_info=True)
        return None

def sanitize_filename_part(text):
    """Basic sanitization for name/version parts of filename."""
    safe_text = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in text)
    safe_text = re.sub(r'_+', '_', safe_text)
    safe_text = safe_text.strip('_-.')
    return safe_text if safe_text else "invalid_name"

def _construct_tgz_filename(name, version):
    """Constructs the standard filename using the sanitized parts."""
    return f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.tgz"

def find_and_extract_sd(tgz_path, resource_identifier):
    """Helper to find and extract SD json from a given tgz path by ID, Name, or Type."""
    sd_data = None
    found_path = None
    logger = logging.getLogger(__name__)
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"File not found in find_and_extract_sd: {tgz_path}")
        return None, None
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            logger.debug(f"Searching for SD matching '{resource_identifier}' in {os.path.basename(tgz_path)}")
            for member in tar:
                if not (member.isfile() and member.name.startswith('package/') and member.name.lower().endswith('.json')):
                    continue
                if os.path.basename(member.name).lower() in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue

                fileobj = None
                try:
                    fileobj = tar.extractfile(member)
                    if fileobj:
                        content_bytes = fileobj.read()
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)
                        if isinstance(data, dict) and data.get('resourceType') == 'StructureDefinition':
                            sd_id = data.get('id')
                            sd_name = data.get('name')
                            sd_type = data.get('type')
                            # Match if requested identifier matches ID, Name, or Base Type
                            if resource_identifier == sd_type or resource_identifier == sd_id or resource_identifier == sd_name:
                                sd_data = data
                                found_path = member.name
                                logger.info(f"Found matching SD for '{resource_identifier}' at path: {found_path}")
                                break  # Stop searching once found
                except Exception as e:
                    logger.warning(f"Could not read/parse potential SD {member.name}: {e}")
                finally:
                    if fileobj:
                        fileobj.close()

            if sd_data is None:
                logger.info(f"SD matching '{resource_identifier}' not found within archive {os.path.basename(tgz_path)} - caller may attempt fallback")
    except tarfile.TarError as e:
        logger.error(f"TarError reading {tgz_path} in find_and_extract_sd: {e}")
        raise tarfile.TarError(f"Error reading package archive: {e}") from e
    except FileNotFoundError as e:
        logger.error(f"FileNotFoundError reading {tgz_path} in find_and_extract_sd: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in find_and_extract_sd for {tgz_path}: {e}", exc_info=True)
        raise
    return sd_data, found_path

def save_package_metadata(name, version, dependency_mode, dependencies, complies_with_profiles=None, imposed_profiles=None):
    """Saves the dependency mode, imported dependencies, and profile relationships as metadata alongside the package."""
    logger = logging.getLogger(__name__)
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Could not get download directory for metadata saving.")
        return False

    metadata = {
        'package_name': name,
        'version': version,
        'dependency_mode': dependency_mode,
        'imported_dependencies': dependencies,
        'complies_with_profiles': complies_with_profiles or [],
        'imposed_profiles': imposed_profiles or []
    }
    metadata_filename = f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.metadata.json"
    metadata_path = os.path.join(download_dir, metadata_filename)
    try:
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        logger.info(f"Saved metadata for {name}#{version} at {metadata_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save metadata for {name}#{version}: {e}")
        return False

def get_package_metadata(name, version):
    """Retrieves the metadata for a given package."""
    logger = logging.getLogger(__name__)
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Could not get download directory for metadata retrieval.")
        return None

    metadata_filename = f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.metadata.json"
    metadata_path = os.path.join(download_dir, metadata_filename)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read metadata for {name}#{version}: {e}")
            return None
    return None

def validate_resource_against_profile(resource, package_name, package_version, resource_type):
    """
    Validate a FHIR resource against a profile and its imposed profiles.
    Returns a dictionary with validation results.
    """
    logger = logging.getLogger(__name__)
    result = {
        'valid': True,
        'errors': [],
        'imposed_profile_results': {}
    }

    # Load the primary profile
    package_filename = f"{sanitize_filename_part(package_name)}-{sanitize_filename_part(package_version)}.tgz"
    package_path = os.path.join(_get_download_dir(), package_filename)
    if not os.path.exists(package_path):
        result['valid'] = False
        result['errors'].append(f"Package not found: {package_name}#{package_version}")
        return result

    # Find the StructureDefinition for the resource type
    sd_filename = f"package/StructureDefinition-{resource_type.lower()}.json"
    if package_name == 'hl7.fhir.us.core':
        sd_filename = f"package/StructureDefinition-us-core-{resource_type.lower()}.json"

    primary_profile_valid = True
    primary_errors = []
    with tarfile.open(package_path, "r:gz") as tar:
        try:
            file_obj = tar.extractfile(sd_filename)
            if file_obj is None:
                raise KeyError(f"StructureDefinition not found: {sd_filename}")
            sd_data = json.load(file_obj)
            # Simplified validation: Check required elements
            snapshot = sd_data.get('snapshot', {})
            for element in snapshot.get('element', []):
                if element.get('min', 0) > 0:  # Required element
                    path = element.get('path')
                    # Check if the path exists in the resource
                    keys = path.split('.')
                    current = resource
                    for key in keys[1:]:  # Skip the resourceType
                        current = current.get(key)
                        if current is None:
                            primary_profile_valid = False
                            primary_errors.append(f"Missing required element {path} in {package_name}#{package_version}")
                            break
        except (KeyError, json.JSONDecodeError) as e:
            primary_profile_valid = False
            primary_errors.append(f"Error loading StructureDefinition: {str(e)}")

    if not primary_profile_valid:
        result['valid'] = False
        result['errors'].extend(primary_errors)

    # Check imposed profiles if validation is enabled
    if not current_app.config.get('VALIDATE_IMPOSED_PROFILES', True):
        logger.info("Imposed profile validation is disabled via configuration.")
        return result

    metadata_filename = f"{sanitize_filename_part(package_name)}-{sanitize_filename_part(package_version)}.metadata.json"
    metadata_path = os.path.join(_get_download_dir(), metadata_filename)
    if not os.path.exists(metadata_path):
        logger.warning(f"Metadata not found for {package_name}#{package_version}, skipping imposed profile validation.")
        return result

    with open(metadata_path, 'r') as f:
        metadata = json.load(f)
    imposed_profiles = metadata.get('imposed_profiles', [])

    for imposed_url in imposed_profiles:
        # Parse the canonical URL to get package name and version
        # Example: http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient|3.1.1
        try:
            imposed_package, imposed_version = parse_canonical_url(imposed_url)
        except ValueError as e:
            result['errors'].append(f"Invalid canonical URL for imposed profile: {imposed_url} - {str(e)}")
            continue

        imposed_result = validate_resource_against_profile(resource, imposed_package, imposed_version, resource_type)
        result['imposed_profile_results'][imposed_url] = imposed_result
        if not imposed_result['valid']:
            result['valid'] = False
            result['errors'].extend([f"Failed imposed profile {imposed_url}: {err}" for err in imposed_result['errors']])

    return result

def parse_canonical_url(canonical_url):
    """
    Parse a canonical URL to extract package name and version.
    Example: http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient|3.1.1
    Returns (package_name, version)
    """
    parts = canonical_url.split('|')
    if len(parts) != 2:
        raise ValueError("Canonical URL must include version after '|'")
    version = parts[1]
    path_parts = parts[0].split('/')
    # Extract package name (e.g., hl7.fhir.us.core)
    package_name = '.'.join(path_parts[3:5])  # Adjust based on URL structure
    return package_name, version

# --- Core Service Functions ---

def download_package(name, version):
    """ Downloads a single FHIR package. Returns (save_path, error_message) """
    logger = logging.getLogger(__name__)
    download_dir = _get_download_dir()
    if not download_dir:
        return None, "Could not get/create download directory."

    package_id = f"{name}#{version}"
    package_url = f"{FHIR_REGISTRY_BASE_URL}/{name}/{version}"
    filename = _construct_tgz_filename(name, version)
    save_path = os.path.join(download_dir, filename)

    if os.path.exists(save_path):
        logger.info(f"Exists: {filename}")
        return save_path, None

    logger.info(f"Downloading: {package_id} -> {filename}")
    try:
        with requests.get(package_url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(save_path, 'wb') as f:
                logger.debug(f"Opened {save_path} for writing.")
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        logger.info(f"Success: Downloaded {filename}")
        return save_path, None
    except requests.exceptions.RequestException as e:
        err_msg = f"Download error for {package_id}: {e}"; logger.error(err_msg); return None, err_msg
    except OSError as e:
        err_msg = f"File save error for {filename}: {e}"; logger.error(err_msg); return None, err_msg
    except Exception as e:
        err_msg = f"Unexpected download error for {package_id}: {e}"; logger.error(err_msg, exc_info=True); return None, err_msg

def extract_dependencies(tgz_path):
    """ Extracts dependencies dict from package.json. Returns (dep_dict or None on error, error_message) """
    logger = logging.getLogger(__name__)
    package_json_path = "package/package.json"
    dependencies = {}
    error_message = None
    if not tgz_path or not os.path.exists(tgz_path):
        return None, f"File not found at {tgz_path}"
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            package_json_member = tar.getmember(package_json_path)
            package_json_fileobj = tar.extractfile(package_json_member)
            if package_json_fileobj:
                try:
                    package_data = json.load(package_json_fileobj)
                    dependencies = package_data.get('dependencies', {})
                finally:
                    package_json_fileobj.close()
            else:
                raise FileNotFoundError(f"Could not extract {package_json_path}")
    except KeyError:
        error_message = f"'{package_json_path}' not found in {os.path.basename(tgz_path)}.";
        logger.warning(error_message)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        error_message = f"Parse error in {package_json_path}: {e}"; logger.error(error_message); dependencies = None
    except (tarfile.TarError, FileNotFoundError) as e:
        error_message = f"Archive error {os.path.basename(tgz_path)}: {e}"; logger.error(error_message); dependencies = None
    except Exception as e:
        error_message = f"Unexpected error extracting deps: {e}"; logger.error(error_message, exc_info=True); dependencies = None
    return dependencies, error_message

def extract_used_types(tgz_path):
    """ Extracts all resource types and referenced types from the package to determine used dependencies. """
    logger = logging.getLogger(__name__)
    used_types = set()
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar:
                if not (member.isfile() and member.name.startswith('package/') and member.name.lower().endswith('.json')):
                    continue
                if os.path.basename(member.name).lower() in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue

                fileobj = None
                try:
                    fileobj = tar.extractfile(member)
                    if fileobj:
                        content_bytes = fileobj.read()
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)
                        resource_type = data.get('resourceType')

                        # Add the resource type itself
                        if resource_type:
                            used_types.add(resource_type)

                        # If this is a StructureDefinition, extract referenced types
                        if resource_type == 'StructureDefinition':
                            sd_type = data.get('type')
                            if sd_type:
                                used_types.add(sd_type)

                            # Extract types from elements
                            for element_list in [data.get('snapshot', {}).get('element', []), data.get('differential', {}).get('element', [])]:
                                for element in element_list:
                                    if 'type' in element:
                                        for t in element['type']:
                                            if 'code' in t:
                                                used_types.add(t['code'])
                                            if 'targetProfile' in t:
                                                for profile in t['targetProfile']:
                                                    type_name = profile.split('/')[-1]
                                                    used_types.add(type_name)

                        # If this is another resource (e.g., ValueSet, CodeSystem), extract referenced types
                        else:
                            # Look for meta.profile for referenced profiles
                            profiles = data.get('meta', {}).get('profile', [])
                            for profile in profiles:
                                type_name = profile.split('/')[-1]
                                used_types.add(type_name)

                            # For ValueSet, check compose.include.system
                            if resource_type == 'ValueSet':
                                for include in data.get('compose', {}).get('include', []):
                                    system = include.get('system')
                                    if system and system.startswith('http://hl7.org/fhir/'):
                                        type_name = system.split('/')[-1]
                                        used_types.add(type_name)

                except Exception as e:
                    logger.warning(f"Could not process member {member.name} for used types: {e}")
                finally:
                    if fileobj:
                        fileobj.close()

    except Exception as e:
        logger.error(f"Error extracting used types from {tgz_path}: {e}")
    return used_types

def map_types_to_packages(used_types, all_dependencies):
    """ Maps used types to the packages that provide them based on dependency lists. """
    logger = logging.getLogger(__name__)
    type_to_package = {}
    for (pkg_name, pkg_version), deps in all_dependencies.items():
        for dep_name, dep_version in deps.items():
            for t in used_types:
                if t.lower() in dep_name.lower():
                    type_to_package[t] = (dep_name, dep_version)
        for t in used_types:
            if t.lower() in pkg_name.lower():
                type_to_package[t] = (pkg_name, pkg_version)

    # Fallback: map remaining types to the canonical package
    for t in used_types:
        if t not in type_to_package:
            type_to_package[t] = CANONICAL_PACKAGE

    return type_to_package

# --- Recursive Import Orchestrator ---
def import_package_and_dependencies(initial_name, initial_version, dependency_mode='recursive'):
    """Orchestrates recursive download and dependency extraction based on the dependency mode."""
    logger = logging.getLogger(__name__)
    logger.info(f"Starting import for {initial_name}#{initial_version} with dependency_mode={dependency_mode}")
    results = {
        'requested': (initial_name, initial_version),
        'processed': set(),
        'downloaded': {},
        'all_dependencies': {},
        'dependencies': [],
        'errors': []
    }
    pending_queue = [(initial_name, initial_version)]
    processed_lookup = set()

    # Always download the initial package
    name, version = initial_name, initial_version
    package_id_tuple = (name, version)
    logger.info(f"Processing initial package: {name}#{version}")
    processed_lookup.add(package_id_tuple)
    save_path, dl_error = download_package(name, version)

    if dl_error:
        error_msg = f"Download failed for {name}#{version}: {dl_error}"
        results['errors'].append(error_msg)
        logger.error("Aborting import: Initial package download failed.")
        return results
    else:
        results['downloaded'][package_id_tuple] = save_path
        dependencies, dep_error = extract_dependencies(save_path)
        if dep_error:
            results['errors'].append(f"Dependency extraction failed for {name}#{version}: {dep_error}")
        elif dependencies is not None:
            results['all_dependencies'][package_id_tuple] = dependencies
            results['processed'].add(package_id_tuple)
            logger.debug(f"Dependencies for {name}#{version}: {list(dependencies.keys())}")
            for dep_name, dep_version in dependencies.items():
                if isinstance(dep_name, str) and isinstance(dep_version, str) and dep_name and dep_version:
                    results['dependencies'].append({"name": dep_name, "version": dep_version})

    # Process the package to extract compliesWithProfile and imposeProfile
    package_info = process_package_file(save_path)
    complies_with_profiles = package_info.get('complies_with_profiles', [])
    imposed_profiles = package_info.get('imposed_profiles', [])

    # Save metadata for the initial package with profile relationships
    save_package_metadata(initial_name, initial_version, dependency_mode, results['dependencies'],
                         complies_with_profiles=complies_with_profiles,
                         imposed_profiles=imposed_profiles)

    # Handle dependency pulling based on mode
    if dependency_mode == 'recursive':
        for dep in results['dependencies']:
            dep_name, dep_version = dep['name'], dep['version']
            dep_tuple = (dep_name, dep_version)
            if dep_tuple not in processed_lookup:
                pending_queue.append(dep_tuple)
                logger.debug(f"Added to queue (recursive): {dep_name}#{dep_version}")

    elif dependency_mode == 'patch-canonical':
        canonical_name, canonical_version = CANONICAL_PACKAGE
        canonical_tuple = (canonical_name, canonical_version)
        if canonical_tuple not in processed_lookup:
            pending_queue.append(canonical_tuple)
            logger.debug(f"Added canonical package to queue: {canonical_name}#{canonical_version}")

    elif dependency_mode == 'tree-shaking':
        used_types = extract_used_types(save_path)
        logger.debug(f"Used types in {initial_name}#{initial_version}: {used_types}")
        type_to_package = map_types_to_packages(used_types, results['all_dependencies'])
        logger.debug(f"Type to package mapping: {type_to_package}")
        for t, (dep_name, dep_version) in type_to_package.items():
            dep_tuple = (dep_name, dep_version)
            if dep_tuple not in processed_lookup and dep_tuple != package_id_tuple:
                pending_queue.append(dep_tuple)
                logger.debug(f"Added to queue (tree-shaking): {dep_name}#{dep_version}")

    # Process the queue
    while pending_queue:
        name, version = pending_queue.pop(0)
        package_id_tuple = (name, version)

        if package_id_tuple in processed_lookup:
            continue

        logger.info(f"Processing: {name}#{version}")
        processed_lookup.add(package_id_tuple)

        save_path, dl_error = download_package(name, version)

        if dl_error:
            error_msg = f"Download failed for {name}#{version}: {dl_error}"
            results['errors'].append(error_msg)
            continue
        else:
            results['downloaded'][package_id_tuple] = save_path
            dependencies, dep_error = extract_dependencies(save_path)
            if dep_error:
                results['errors'].append(f"Dependency extraction failed for {name}#{version}: {dep_error}")
            elif dependencies is not None:
                results['all_dependencies'][package_id_tuple] = dependencies
                results['processed'].add(package_id_tuple)
                logger.debug(f"Dependencies for {name}#{version}: {list(dependencies.keys())}")
                for dep_name, dep_version in dependencies.items():
                    if isinstance(dep_name, str) and isinstance(dep_version, str) and dep_name and dep_version:
                        dep_tuple = (dep_name, dep_version)
                        results['dependencies'].append({"name": dep_name, "version": dep_version})
                        if dependency_mode == 'recursive' and dep_tuple not in processed_lookup:
                            pending_queue.append(dep_tuple)
                            logger.debug(f"Added to queue: {dep_name}#{dep_version}")

    proc_count = len(results['processed'])
    dl_count = len(results['downloaded'])
    err_count = len(results['errors'])
    logger.info(f"Import finished. Processed: {proc_count}, Downloaded/Verified: {dl_count}, Errors: {err_count}")
    return results

# --- Package File Content Processor ---
def process_package_file(tgz_path):
    """ Extracts types, profile status, MS elements, examples, and profile relationships from a downloaded .tgz package. """
    logger = logging.getLogger(__name__)
    logger.info(f"Processing package file details: {tgz_path}")

    results = {
        'resource_types_info': [],
        'must_support_elements': {},
        'examples': {},
        'complies_with_profiles': [],
        'imposed_profiles': [],
        'errors': []
    }
    resource_info = defaultdict(lambda: {
        'name': None,
        'type': None,
        'is_profile': False,
        'ms_flag': False,
        'ms_paths': set(),
        'examples': set()
    })

    if not tgz_path or not os.path.exists(tgz_path):
        results['errors'].append(f"Package file not found: {tgz_path}")
        return results

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar:
                if not member.isfile() or not member.name.startswith('package/') or not member.name.lower().endswith(('.json', '.xml', '.html')):
                    continue
                member_name_lower = member.name.lower()
                base_filename_lower = os.path.basename(member_name_lower)
                fileobj = None
                if base_filename_lower in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue

                is_example = member.name.startswith('package/example/') or 'example' in base_filename_lower
                is_json = member_name_lower.endswith('.json')

                try:
                    if is_json:
                        fileobj = tar.extractfile(member)
                        if not fileobj:
                            continue
                        content_bytes = fileobj.read()
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)
                        if not isinstance(data, dict) or 'resourceType' not in data:
                            continue

                        resource_type = data['resourceType']
                        entry_key = resource_type
                        is_sd = False

                        if resource_type == 'StructureDefinition':
                            is_sd = True
                            profile_id = data.get('id') or data.get('name')
                            sd_type = data.get('type')
                            sd_base = data.get('baseDefinition')
                            is_profile_sd = bool(sd_base)
                            if not profile_id or not sd_type:
                                logger.warning(f"SD missing ID or Type: {member.name}")
                                continue
                            entry_key = profile_id

                            # Extract compliesWithProfile and imposeProfile extensions
                            complies_with = []
                            imposed_profiles = []
                            for ext in data.get('extension', []):
                                if ext.get('url') == 'http://hl7.org/fhir/StructureDefinition/structuredefinition-compliesWithProfile':
                                    value = ext.get('valueCanonical')
                                    if value:
                                        complies_with.append(value)
                                elif ext.get('url') == 'http://hl7.org/fhir/StructureDefinition/structuredefinition-imposeProfile':
                                    value = ext.get('valueCanonical')
                                    if value:
                                        imposed_profiles.append(value)

                            # Store the relationships
                            if complies_with:
                                results['complies_with_profiles'].extend(complies_with)
                            if imposed_profiles:
                                results['imposed_profiles'].extend(imposed_profiles)

                        entry = resource_info[entry_key]
                        entry.setdefault('type', resource_type)

                        if is_sd:
                            entry['name'] = entry_key
                            entry['type'] = sd_type
                            entry['is_profile'] = is_profile_sd
                            if not entry.get('sd_processed'):
                                has_ms = False
                                ms_paths_for_sd = set()
                                for element_list in [data.get('snapshot', {}).get('element', []), data.get('differential', {}).get('element', [])]:
                                    for element in element_list:
                                        if isinstance(element, dict) and element.get('mustSupport') is True:
                                            element_path = element.get('path')
                                            if element_path:
                                                ms_paths_for_sd.add(element_path)
                                                has_ms = True
                                            else:
                                                logger.warning(f"Found mustSupport=true without path in element of {entry_key}")
                                if ms_paths_for_sd:
                                    entry['ms_paths'] = ms_paths_for_sd
                                if has_ms:
                                    entry['ms_flag'] = True
                                    logger.debug(f"  Found MS elements in {entry_key}")
                                entry['sd_processed'] = True

                        elif is_example:
                            key_to_use = None
                            profile_meta = data.get('meta', {}).get('profile', [])
                            if profile_meta and isinstance(profile_meta, list):
                                for profile_url in profile_meta:
                                    profile_id_from_meta = profile_url.split('/')[-1]
                                    if profile_id_from_meta in resource_info:
                                        key_to_use = profile_id_from_meta
                                        break
                            if not key_to_use:
                                key_to_use = resource_type
                            if key_to_use not in resource_info:
                                resource_info[key_to_use].update({'name': key_to_use, 'type': resource_type})
                            resource_info[key_to_use]['examples'].add(member.name)

                    elif is_example:
                        guessed_type = base_filename_lower.split('-')[0].capitalize()
                        guessed_profile_id = base_filename_lower.split('-')[0]
                        key_to_use = None
                        if guessed_profile_id in resource_info:
                            key_to_use = guessed_profile_id
                        elif guessed_type in resource_info:
                            key_to_use = guessed_type
                        if key_to_use:
                            resource_info[key_to_use]['examples'].add(member.name)
                        else:
                            logger.warning(f"Could not associate non-JSON example {member.name}")

                except Exception as e:
                    logger.warning(f"Could not process member {member.name}: {e}", exc_info=False)
                finally:
                    if fileobj:
                        fileobj.close()

        # Final formatting
        final_list = []
        final_ms_elements = {}
        final_examples = {}
        logger.debug(f"Formatting results from resource_info keys: {list(resource_info.keys())}")
        for key, info in resource_info.items():
            display_name = info.get('name') or key
            base_type = info.get('type')
            if display_name or base_type:
                logger.debug(f"  Formatting item '{display_name}': type='{base_type}', profile='{info.get('is_profile', False)}', ms_flag='{info.get('ms_flag', False)}'")
                final_list.append({
                    'name': display_name,
                    'type': base_type,
                    'is_profile': info.get('is_profile', False),
                    'must_support': info.get('ms_flag', False)
                })
                if info['ms_paths']:
                    final_ms_elements[display_name] = sorted(list(info['ms_paths']))
                if info['examples']:
                    final_examples[display_name] = sorted(list(info['examples']))
            else:
                logger.warning(f"Skipping formatting for key: {key}")

        results['resource_types_info'] = sorted(final_list, key=lambda x: (not x.get('is_profile', False), x.get('name', '')))
        results['must_support_elements'] = final_ms_elements
        results['examples'] = final_examples

    except Exception as e:
        err_msg = f"Error processing package file {tgz_path}: {e}"
        logger.error(err_msg, exc_info=True)
        results['errors'].append(err_msg)

    # Logging counts
    final_types_count = len(results['resource_types_info'])
    ms_count = sum(1 for r in results['resource_types_info'] if r['must_support'])
    total_ms_paths = sum(len(v) for v in results['must_support_elements'].values())
    total_examples = sum(len(v) for v in results['examples'].values())
    logger.info(f"Extraction: {final_types_count} items ({ms_count} MS; {total_ms_paths} MS paths; {total_examples} examples) from {os.path.basename(tgz_path)}")

    return results