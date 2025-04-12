# app/services.py

import requests
import os
import tarfile
import json
import re
import logging
from flask import current_app
from collections import defaultdict
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
FHIR_REGISTRY_BASE_URL = "https://packages.fhir.org"
DOWNLOAD_DIR_NAME = "fhir_packages"
CANONICAL_PACKAGE = ("hl7.fhir.r4.core", "4.0.1")  # Define the canonical FHIR package

# --- Helper Functions ---

def _get_download_dir():
    """Gets the absolute path to the download directory, creating it if needed."""
    instance_path = None
    try:
        # Try to get instance_path from Flask app context if available
        instance_path = current_app.instance_path
        logger.debug(f"Using instance path from current_app: {instance_path}")
    except RuntimeError:
        # Fallback if no app context (e.g., running script directly)
        logger.warning("No app context for instance_path, constructing relative path.")
        # Assume services.py is in /app, instance folder sibling to /app
        instance_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'instance'))
        logger.debug(f"Constructed instance path: {instance_path}")

    if not instance_path:
        logger.error("Fatal Error: Could not determine instance path.")
        return None

    download_dir = os.path.join(instance_path, DOWNLOAD_DIR_NAME)
    try:
        os.makedirs(download_dir, exist_ok=True)
        # Add check for flask config path
        if 'FHIR_PACKAGES_DIR' not in current_app.config:
             current_app.config['FHIR_PACKAGES_DIR'] = download_dir
             logger.info(f"Set current_app.config['FHIR_PACKAGES_DIR'] to {download_dir}")
        return download_dir
    except OSError as e:
        logger.error(f"Fatal Error creating dir {download_dir}: {e}", exc_info=True)
        return None
    except RuntimeError: # Catch if current_app doesn't exist here either
         logger.warning("No app context available to set FHIR_PACKAGES_DIR config.")
         # Still attempt to create and return the path for non-Flask use cases
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
                        # Handle potential BOM (Byte Order Mark)
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)
                        if isinstance(data, dict) and data.get('resourceType') == 'StructureDefinition':
                            sd_id = data.get('id')
                            sd_name = data.get('name')
                            sd_type = data.get('type') # The type the SD describes (e.g., Patient)
                            # Match if requested identifier matches ID, Name, or the Base Type the SD describes
                            # Case-insensitive matching might be safer for identifiers
                            if resource_identifier and (resource_identifier.lower() == str(sd_type).lower() or
                                                       resource_identifier.lower() == str(sd_id).lower() or
                                                       resource_identifier.lower() == str(sd_name).lower()):
                                sd_data = data
                                found_path = member.name
                                logger.info(f"Found matching SD for '{resource_identifier}' at path: {found_path} (Matched on Type/ID/Name)")
                                break  # Stop searching once found
                except json.JSONDecodeError as e:
                     logger.warning(f"Could not parse JSON in {member.name}: {e}")
                except UnicodeDecodeError as e:
                     logger.warning(f"Could not decode UTF-8 in {member.name}: {e}")
                except tarfile.TarError as e:
                     logger.warning(f"Tar error reading member {member.name}: {e}")
                     # Potentially break or continue depending on severity preference
                except Exception as e:
                    logger.warning(f"Could not read/parse potential SD {member.name}: {e}")
                finally:
                    if fileobj:
                        fileobj.close()

            if sd_data is None:
                logger.info(f"SD matching '{resource_identifier}' not found within archive {os.path.basename(tgz_path)} - caller may attempt fallback")
    except tarfile.ReadError as e:
        logger.error(f"Tar ReadError (possibly corrupted file) reading {tgz_path}: {e}")
        # Decide if this should raise or return None
        return None, None # Or raise custom error
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
        with open(metadata_path, 'w', encoding='utf-8') as f: # Specify encoding
            json.dump(metadata, f, indent=2)
        logger.info(f"Saved metadata for {name}#{version} at {metadata_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save metadata for {name}#{version}: {e}")
        return False

def get_package_metadata(name, version):
    """Retrieves the metadata for a given package."""
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Could not get download directory for metadata retrieval.")
        return None

    metadata_filename = f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.metadata.json"
    metadata_path = os.path.join(download_dir, metadata_filename)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f: # Specify encoding
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read metadata for {name}#{version}: {e}")
            return None
    return None

# --- New navigate_fhir_path ---
def navigate_fhir_path(resource, path):
    """Navigate a FHIR resource path, handling arrays, nested structures, and choice types."""
    keys = path.split('.')
    # Remove the root resource type if present (e.g., Patient.name -> name)
    if keys and resource and isinstance(resource, dict) and keys[0] == resource.get('resourceType'):
        keys = keys[1:]

    current = resource

    for i, key in enumerate(keys):
        is_last_key = (i == len(keys) - 1)
        # logger.debug(f"Navigating: key='{key}', is_last={is_last_key}, current_type={type(current)}") # Uncomment for debug

        if current is None:
             # logger.debug(f"Navigation stopped, current became None before processing key '{key}'.")
             return None

        if isinstance(current, dict):
            # Handle direct key access
            if key in current:
                current = current.get(key) # Use .get() for safety
            # Handle choice type e.g., value[x]
            elif '[x]' in key:
                base_key = key.replace('[x]', '')
                found_choice = False
                for k, v in current.items():
                    if k.startswith(base_key):
                        current = v
                        found_choice = True
                        break
                if not found_choice:
                    # logger.debug(f"Choice key '{key}' (base: {base_key}) not found in dict keys: {list(current.keys())}")
                    return None
            else:
                # logger.debug(f"Key '{key}' not found in dict keys: {list(current.keys())}")
                return None

        elif isinstance(current, list):
            # If it's the last key, the path refers to the list itself.
            # The validation logic needs to handle checking the list.
            if is_last_key:
                 # logger.debug(f"Path ends on a list for key '{key}'. Returning list: {current}")
                 return current # Return the list itself for the validator to check

            # --- If not the last key, we need to look inside list elements ---
            # This is tricky. FHIRPath has complex list navigation.
            # For simple validation (does element X exist?), we might assume
            # we just need to find *one* item in the list that has the subsequent path.
            # Let's try finding the first match within the list.
            found_in_list = False
            results_from_list = []
            remaining_path = '.'.join(keys[i:]) # The rest of the path including current key
            # logger.debug(f"List encountered for key '{key}'. Searching elements for remaining path: '{remaining_path}'")

            for item in current:
                # Recursively navigate into the item using the *remaining* path
                sub_result = navigate_fhir_path(item, remaining_path)
                if sub_result is not None:
                    # Collect all non-None results if validating cardinality or specific values later
                    if isinstance(sub_result, list):
                         results_from_list.extend(sub_result)
                    else:
                         results_from_list.append(sub_result)
                    # For basic existence check, finding one is enough, but let's collect all
                    # found_in_list = True
                    # break # Or collect all? Let's collect for now.

            if not results_from_list:
                 # logger.debug(f"Remaining path '{remaining_path}' not found in any list items.")
                 return None # Path not found in any list element

            # What to return? The first result? All results?
            # If the final part of the path should be a single value, return first.
            # If it could be multiple (e.g., Patient.name.given returns multiple strings), return list.
            # Let's return the list of found items. The validator can check if it's non-empty.
            # logger.debug(f"Found results in list for '{remaining_path}': {results_from_list}")
            return results_from_list # Return list of found values/sub-structures


        else:
            # Current is not a dict or list, cannot navigate further
            # logger.debug(f"Cannot navigate further, current is not dict/list (key='{key}').")
            return None

    # logger.debug(f"Final result for path '{path}': {current}")
    return current

# --- End New navigate_fhir_path ---


def validate_resource_against_profile(package_name, version, resource, include_dependencies=True):
    """Validate a single FHIR resource against a package's StructureDefinitions."""
    logger.debug(f"Starting validation for resource: {resource.get('resourceType')}/{resource.get('id')} against {package_name}#{version}")
    try:
        # Find the resource's type
        resource_type = resource.get('resourceType')
        if not resource_type:
            return {'valid': False, 'errors': ['Resource is missing resourceType.'], 'warnings': []}

        # Get StructureDefinition
        # Ensure download dir is fetched and config potentially set
        download_dir = _get_download_dir()
        if not download_dir:
             return {'valid': False, 'errors': ['Could not determine FHIR package directory.'], 'warnings': []}

        # Construct path using helper for consistency
        tgz_filename = _construct_tgz_filename(package_name, version)
        # Use absolute path from download_dir
        tgz_path = os.path.join(download_dir, tgz_filename)

        logger.debug(f"Attempting to load SD for type '{resource_type}' from tgz: {tgz_path}")
        sd_data, sd_path_in_tar = find_and_extract_sd(tgz_path, resource_type)
        if not sd_data:
            logger.error(f"No StructureDefinition found for type '{resource_type}' in package {package_name}#{version} at {tgz_path}")
            # Try falling back to canonical package if not the one requested? Maybe not here.
            return {'valid': False, 'errors': [f"StructureDefinition for resource type '{resource_type}' not found in package {package_name}#{version}."], 'warnings': []}
        logger.debug(f"Found SD for '{resource_type}' in tar at '{sd_path_in_tar}'")

        # Prefer snapshot if available, otherwise use differential
        elements = sd_data.get('snapshot', {}).get('element', [])
        if not elements:
             elements = sd_data.get('differential', {}).get('element', [])
             logger.debug("Using differential elements for validation (snapshot missing).")
        if not elements:
             logger.error(f"StructureDefinition {sd_data.get('id', resource_type)} has no snapshot or differential elements.")
             return {'valid': False, 'errors': [f"StructureDefinition '{sd_data.get('id', resource_type)}' is invalid (no elements)."], 'warnings': []}

        must_support_paths = []
        for element in elements:
            if element.get('mustSupport', False):
                path = element.get('path', '')
                if path:
                    must_support_paths.append(path)

        errors = []
        warnings = []

        # --- Revised Required Field Validation (min >= 1) ---
        logger.debug(f"Checking required fields for {resource_type} based on SD {sd_data.get('id')}...")
        element_definitions = {e.get('path'): e for e in elements if e.get('path')} # Cache elements by path

        for element in elements:
            path = element.get('path', '')
            min_val = element.get('min', 0)
            # Skip base element (e.g., "Patient") as it's always present if resourceType matches
            if '.' not in path:
                continue

            if min_val >= 1:
                logger.debug(f"Checking required path: {path} (min={min_val})")

                # --- START: Parent Presence Check ---
                parent_path = '.'.join(path.split('.')[:-1])
                parent_is_present_or_not_applicable = True # Assume true unless parent is optional AND absent

                # Check only if parent_path is a valid element path (not just the root type)
                if '.' in parent_path:
                    parent_element_def = element_definitions.get(parent_path)
                    if parent_element_def:
                        parent_min_val = parent_element_def.get('min', 0)
                        # If the parent element itself is optional (min: 0)...
                        if parent_min_val == 0:
                            # ...check if the parent element actually exists in the instance data
                            parent_value = navigate_fhir_path(resource, parent_path)
                            if parent_value is None or (isinstance(parent_value, (list, str, dict)) and not parent_value):
                                # Optional parent is missing, so child cannot be required. Skip the check for this element.
                                parent_is_present_or_not_applicable = False
                                logger.debug(f"-> Requirement check for '{path}' skipped: Optional parent '{parent_path}' is absent.")
                    else:
                        # This case indicates an issue with the SD structure or path generation, but we'll be lenient
                        logger.warning(f"Could not find definition for parent path '{parent_path}' while checking requirement for '{path}'. Proceeding with check.")
                # --- END: Parent Presence Check ---

                # Only proceed with checking the element itself if its optional parent is present,
                # or if the parent is required, or if it's a top-level element.
                if parent_is_present_or_not_applicable:
                    value = navigate_fhir_path(resource, path)

                    # 1. Check for presence (is it None or an empty container?)
                    is_missing_or_empty = False
                    if value is None:
                        is_missing_or_empty = True
                        logger.debug(f"-> Path '{path}' value is None.")
                    elif isinstance(value, (list, str, dict)) and not value:
                        is_missing_or_empty = True
                        logger.debug(f"-> Path '{path}' value is an empty {type(value).__name__}.")
                    elif isinstance(value, bool) and value is False: pass # Valid presence
                    elif isinstance(value, (int, float)) and value == 0: pass # Valid presence

                    if is_missing_or_empty:
                        # Log the error only if the parent context allowed the check
                        errors.append(f"Required field '{path}' is missing or empty.")
                        logger.warning(f"Validation Error: Required field '{path}' missing or empty (Context: Parent '{parent_path}' required or present).")
                        continue # Skip further checks for this element if missing

                    # 2. Check specific FHIR types if present (value is not None/empty)
                    # (This part of the logic remains the same as before)
                    element_types = element.get('type', [])
                    type_codes = {t.get('code') for t in element_types if t.get('code')}
                    is_codeable_concept = 'CodeableConcept' in type_codes
                    is_reference = 'Reference' in type_codes
                    is_coding = 'Coding' in type_codes

                    if is_codeable_concept and isinstance(value, dict):
                        codings = value.get('coding')
                        if not value.get('text'):
                            if not isinstance(codings, list) or not any(isinstance(c, dict) and c.get('code') and c.get('system') for c in codings):
                                errors.append(f"Required CodeableConcept '{path}' lacks text or a valid coding (must include system and code).")
                                logger.warning(f"Validation Error: Required CC '{path}' invalid structure.")
                    elif is_coding and isinstance(value, dict):
                         if not value.get('code') or not value.get('system'):
                             errors.append(f"Required Coding '{path}' lacks a system or code.")
                             logger.warning(f"Validation Error: Required Coding '{path}' invalid structure.")
                    elif is_reference and isinstance(value, dict):
                         if not value.get('reference') and not value.get('identifier'):
                             errors.append(f"Required Reference '{path}' lacks a reference or identifier.")
                             logger.warning(f"Validation Error: Required Reference '{path}' invalid structure.")

        # --- Revised Must-Support Field Validation ---
        logger.debug(f"Checking must-support fields for {resource_type}...")
        unique_must_support_paths = sorted(list(set(must_support_paths))) # Avoid duplicate checks if in both snapshot/diff
        for path in unique_must_support_paths:
            # Skip base element
            if '.' not in path:
                continue

            logger.debug(f"Checking must-support path: {path}")
            value = navigate_fhir_path(resource, path)

            # 1. Check for presence
            is_missing_or_empty = False
            if value is None:
                is_missing_or_empty = True
                logger.debug(f"-> Path '{path}' value is None.")
            elif isinstance(value, (list, str, dict)) and not value:
                is_missing_or_empty = True
                logger.debug(f"-> Path '{path}' value is an empty {type(value).__name__}.")
            elif isinstance(value, bool) and value is False:
                 pass
            elif isinstance(value, (int, float)) and value == 0:
                 pass

            if is_missing_or_empty:
                warnings.append(f"Must-support field '{path}' is missing or empty.")
                logger.info(f"Validation Warning: Must-support field '{path}' missing or empty.") # Use INFO for MS warnings
                continue

            # 2. Check specific FHIR types (similar logic to required checks)
            element_def = next((e for e in elements if e.get('path') == path), None)
            if element_def:
                element_types = element_def.get('type', [])
                type_codes = {t.get('code') for t in element_types if t.get('code')}

                is_codeable_concept = 'CodeableConcept' in type_codes
                is_reference = 'Reference' in type_codes
                is_coding = 'Coding' in type_codes

                if is_codeable_concept and isinstance(value, dict):
                    codings = value.get('coding')
                    if not value.get('text'):
                        if not isinstance(codings, list) or not any(isinstance(c, dict) and c.get('code') and c.get('system') for c in codings):
                           warnings.append(f"Must-support CodeableConcept '{path}' lacks text or a valid coding (must include system and code).")
                           logger.info(f"Validation Warning: Must-support CC '{path}' invalid structure.")
                elif is_coding and isinstance(value, dict):
                     if not value.get('code') or not value.get('system'):
                         warnings.append(f"Must-support Coding '{path}' lacks a system or code.")
                         logger.info(f"Validation Warning: Must-support Coding '{path}' invalid structure.")
                elif is_reference and isinstance(value, dict):
                     if not value.get('reference') and not value.get('identifier'):
                         warnings.append(f"Must-support Reference '{path}' lacks a reference or identifier.")
                         logger.info(f"Validation Warning: Must-support Reference '{path}' invalid structure.")


        # --- Dependency Validation ---
        if include_dependencies:
            logger.debug("Checking dependencies...")
            metadata_path = Path(download_dir) / f"{sanitize_filename_part(package_name)}-{sanitize_filename_part(version)}.metadata.json"
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                    for dep in metadata.get('imported_dependencies', []):
                        dep_name = dep.get('name')
                        dep_version = dep.get('version')
                        if not dep_name or not dep_version:
                             logger.warning(f"Skipping invalid dependency entry: {dep}")
                             continue
                        logger.debug(f"Recursively validating against dependency: {dep_name}#{dep_version}")
                        # Pass include_dependencies=False to prevent infinite loops
                        dep_result = validate_resource_against_profile(dep_name, dep_version, resource, include_dependencies=False)
                        if not dep_result['valid']:
                            errors.extend([f"(Dependency {dep_name}#{dep_version}): {e}" for e in dep_result['errors']])
                        # Carry over warnings from dependencies as well
                        warnings.extend([f"(Dependency {dep_name}#{dep_version}): {w}" for w in dep_result['warnings']])
                except Exception as e:
                     logger.error(f"Failed to load or process metadata {metadata_path} for dependencies: {e}")
                     errors.append(f"Failed to process dependency metadata for {package_name}#{version}.")
            else:
                 logger.warning(f"Metadata file not found, cannot validate dependencies: {metadata_path}")


        final_valid_state = len(errors) == 0
        logger.info(f"Validation result for {resource_type}/{resource.get('id')} against {package_name}#{version}: Valid={final_valid_state}, Errors={len(errors)}, Warnings={len(warnings)}")

        return {
            'valid': final_valid_state,
            'errors': errors,
            'warnings': warnings
        }
    except FileNotFoundError:
         # Specific handling if the tgz file itself wasn't found earlier
         logger.error(f"Validation failed: Package file not found for {package_name}#{version}")
         return {'valid': False, 'errors': [f"Package file for {package_name}#{version} not found."], 'warnings': []}
    except tarfile.TarError as e:
         logger.error(f"Validation failed due to TarError for {package_name}#{version}: {e}")
         return {'valid': False, 'errors': [f"Error reading package archive for {package_name}#{version}: {e}"], 'warnings': []}
    except Exception as e:
        logger.error(f"Unexpected error during validation of {resource.get('resourceType')}/{resource.get('id')} against {package_name}#{version}: {e}", exc_info=True)
        return {'valid': False, 'errors': [f'Unexpected validation error: {str(e)}'], 'warnings': []}


def validate_bundle_against_profile(package_name, version, bundle, include_dependencies=True):
    """Validate a FHIR Bundle against a package's StructureDefinitions."""
    try:
        if not isinstance(bundle, dict) or bundle.get('resourceType') != 'Bundle':
            return {'valid': False, 'errors': ['Not a valid Bundle resource.'], 'warnings': [], 'results': {}}

        results = {}
        all_errors = []
        all_warnings = []
        bundle_valid = True

        # Validate each entry's resource
        logger.info(f"Validating Bundle/{bundle.get('id', 'N/A')} against {package_name}#{version}. Entries: {len(bundle.get('entry', []))}")
        for i, entry in enumerate(bundle.get('entry', [])):
            resource = entry.get('resource')
            entry_id = f"Entry {i}"
            resource_id_str = None

            if not resource:
                all_errors.append(f"{entry_id}: Missing 'resource' key in entry.")
                bundle_valid = False
                continue

            if not isinstance(resource, dict):
                 all_errors.append(f"{entry_id}: 'resource' key does not contain a valid FHIR resource (must be a dictionary).")
                 bundle_valid = False
                 continue

            resource_type = resource.get('resourceType')
            resource_id = resource.get('id')
            resource_id_str = f"{resource_type}/{resource_id}" if resource_type and resource_id else resource_type or f"Unnamed Resource in {entry_id}"
            entry_id = f"Entry {i} ({resource_id_str})" # More descriptive ID

            logger.debug(f"Validating {entry_id}...")
            result = validate_resource_against_profile(package_name, version, resource, include_dependencies)
            results[entry_id] = result # Store result keyed by descriptive entry ID
            if not result['valid']:
                bundle_valid = False
                all_errors.extend([f"{entry_id}: {e}" for e in result['errors']])
            all_warnings.extend([f"{entry_id}: {w}" for w in result['warnings']])

        # Validate Bundle structure itself (can add more checks based on profile if needed)
        if not bundle.get('type'):
            all_errors.append("Bundle resource itself is missing the required 'type' field.")
            bundle_valid = False

        logger.info(f"Bundle validation finished. Overall Valid: {bundle_valid}, Total Errors: {len(all_errors)}, Total Warnings: {len(all_warnings)}")
        return {
            'valid': bundle_valid,
            'errors': all_errors,
            'warnings': all_warnings,
            'results': results # Contains individual resource validation results
        }
    except Exception as e:
        logger.error(f"Unexpected error during bundle validation: {str(e)}", exc_info=True)
        return {'valid': False, 'errors': [f'Unexpected bundle validation error: {str(e)}'], 'warnings': [], 'results': {}}


def download_package(name, version):
    """Downloads a single FHIR package. Returns (save_path, error_message)"""
    download_dir = _get_download_dir()
    if not download_dir:
        return None, "Could not get/create download directory."

    package_id = f"{name}#{version}"
    package_url = f"{FHIR_REGISTRY_BASE_URL}/{name}/{version}"
    filename = _construct_tgz_filename(name, version)
    save_path = os.path.join(download_dir, filename)

    if os.path.exists(save_path):
        # Optional: Add size check or hash check for existing files?
        logger.info(f"Package already exists locally: {filename}")
        return save_path, None

    logger.info(f"Downloading: {package_id} from {package_url} -> {filename}")
    try:
        # Use a session for potential keep-alive benefits
        with requests.Session() as session:
             with session.get(package_url, stream=True, timeout=90) as r:
                 r.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
                 # Check content type? Should be application/gzip or similar
                 content_type = r.headers.get('Content-Type', '').lower()
                 if 'gzip' not in content_type and 'tar' not in content_type:
                      logger.warning(f"Unexpected Content-Type '{content_type}' for {package_url}")

                 # Write to temp file first? Prevents partial downloads being seen as complete.
                 # temp_save_path = save_path + ".part"
                 with open(save_path, 'wb') as f:
                     logger.debug(f"Opened {save_path} for writing.")
                     bytes_downloaded = 0
                     for chunk in r.iter_content(chunk_size=8192):
                         # filter out keep-alive new chunks
                         if chunk:
                             f.write(chunk)
                             bytes_downloaded += len(chunk)
                     logger.debug(f"Finished writing {bytes_downloaded} bytes to {save_path}")
                 # os.rename(temp_save_path, save_path) # Move temp file to final location

        # Basic check after download
        if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
             err_msg = f"Download failed for {package_id}: Saved file is missing or empty."
             logger.error(err_msg)
             # Clean up empty file?
             try: os.remove(save_path)
             except OSError: pass
             return None, err_msg

        logger.info(f"Success: Downloaded {filename}")
        return save_path, None

    except requests.exceptions.HTTPError as e:
         # Handle specific HTTP errors like 404 Not Found
         err_msg = f"HTTP error downloading {package_id}: {e}"
         logger.error(err_msg)
         return None, err_msg
    except requests.exceptions.ConnectionError as e:
         err_msg = f"Connection error downloading {package_id}: {e}"
         logger.error(err_msg)
         return None, err_msg
    except requests.exceptions.Timeout as e:
         err_msg = f"Timeout downloading {package_id}: {e}"
         logger.error(err_msg)
         return None, err_msg
    except requests.exceptions.RequestException as e:
        err_msg = f"General download error for {package_id}: {e}"
        logger.error(err_msg)
        return None, err_msg
    except OSError as e:
        err_msg = f"File save error for {filename}: {e}"
        logger.error(err_msg)
        # Clean up partial file if it exists
        if os.path.exists(save_path):
             try: os.remove(save_path)
             except OSError: pass
        return None, err_msg
    except Exception as e:
        err_msg = f"Unexpected download error for {package_id}: {e}"
        logger.error(err_msg, exc_info=True)
        # Clean up partial file
        if os.path.exists(save_path):
             try: os.remove(save_path)
             except OSError: pass
        return None, err_msg

def extract_dependencies(tgz_path):
    """Extracts dependencies dict from package.json. Returns (dep_dict or None on error, error_message)"""
    package_json_path = "package/package.json"
    dependencies = None # Default to None
    error_message = None
    if not tgz_path or not os.path.exists(tgz_path):
        return None, f"File not found at {tgz_path}"
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            # Check if package.json exists before trying to extract
            try:
                 package_json_member = tar.getmember(package_json_path)
            except KeyError:
                 # This is common for core packages like hl7.fhir.r4.core
                 logger.info(f"'{package_json_path}' not found in {os.path.basename(tgz_path)}. Assuming no dependencies.")
                 return {}, None # Return empty dict, no error

            package_json_fileobj = tar.extractfile(package_json_member)
            if package_json_fileobj:
                try:
                    # Read bytes and decode carefully
                    content_bytes = package_json_fileobj.read()
                    content_string = content_bytes.decode('utf-8-sig')
                    package_data = json.loads(content_string)
                    dependencies = package_data.get('dependencies', {})
                    if not isinstance(dependencies, dict):
                         logger.error(f"Invalid 'dependencies' format in {package_json_path} (expected dict, got {type(dependencies)}).")
                         dependencies = None
                         error_message = f"Invalid 'dependencies' format in {package_json_path}."
                except json.JSONDecodeError as e:
                    error_message = f"JSON parse error in {package_json_path}: {e}"
                    logger.error(error_message)
                    dependencies = None
                except UnicodeDecodeError as e:
                    error_message = f"Encoding error reading {package_json_path}: {e}"
                    logger.error(error_message)
                    dependencies = None
                finally:
                    package_json_fileobj.close()
            else:
                # Should not happen if getmember succeeded, but handle defensively
                error_message = f"Could not extract {package_json_path} despite being listed in tar."
                logger.error(error_message)
                dependencies = None

    except tarfile.ReadError as e: # Often indicates corrupted file
        error_message = f"Tar ReadError (possibly corrupted) for {os.path.basename(tgz_path)}: {e}"
        logger.error(error_message)
        dependencies = None
    except tarfile.TarError as e:
        error_message = f"TarError processing {os.path.basename(tgz_path)}: {e}"
        logger.error(error_message)
        dependencies = None
    except FileNotFoundError: # Should be caught by initial check, but include
        error_message = f"Package file not found during dependency extraction: {tgz_path}"
        logger.error(error_message)
        dependencies = None
    except Exception as e:
        error_message = f"Unexpected error extracting deps from {os.path.basename(tgz_path)}: {e}"
        logger.error(error_message, exc_info=True)
        dependencies = None

    return dependencies, error_message


def extract_used_types(tgz_path):
    """Extracts all resource types and referenced types from the package resources."""
    used_types = set()
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"Cannot extract used types: File not found at {tgz_path}")
        return used_types # Return empty set

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar:
                # Process only JSON files within the 'package/' directory
                if not (member.isfile() and member.name.startswith('package/') and member.name.lower().endswith('.json')):
                    continue
                # Skip metadata files
                if os.path.basename(member.name).lower() in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue

                fileobj = None
                try:
                    fileobj = tar.extractfile(member)
                    if fileobj:
                        content_bytes = fileobj.read()
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)

                        if not isinstance(data, dict): continue # Skip if not a valid JSON object

                        resource_type = data.get('resourceType')
                        if not resource_type: continue # Skip if no resourceType

                        # Add the resource type itself
                        used_types.add(resource_type)

                        # --- StructureDefinition Specific Extraction ---
                        if resource_type == 'StructureDefinition':
                            # Add the type this SD defines/constrains
                            sd_type = data.get('type')
                            if sd_type: used_types.add(sd_type)
                            # Add the base definition type if it's a profile
                            base_def = data.get('baseDefinition')
                            if base_def:
                                base_type = base_def.split('/')[-1]
                                # Avoid adding primitive types like 'Element', 'Resource' etc. if not needed
                                if base_type and base_type[0].isupper():
                                     used_types.add(base_type)

                            # Extract types from elements (snapshot or differential)
                            elements = data.get('snapshot', {}).get('element', []) or data.get('differential', {}).get('element', [])
                            for element in elements:
                                if isinstance(element, dict) and 'type' in element:
                                    for t in element.get('type', []):
                                        # Add code (element type)
                                        code = t.get('code')
                                        if code and code[0].isupper(): used_types.add(code)
                                        # Add targetProfile types (Reference targets)
                                        for profile_uri in t.get('targetProfile', []):
                                             if profile_uri:
                                                  profile_type = profile_uri.split('/')[-1]
                                                  if profile_type and profile_type[0].isupper(): used_types.add(profile_type)
                                # Add types from contentReference
                                content_ref = element.get('contentReference')
                                if content_ref and content_ref.startswith('#'):
                                     # This usually points to another element path within the same SD
                                     # Trying to resolve this fully can be complex.
                                     # We might infer types based on the path referenced if needed.
                                     pass

                        # --- General Resource Type Extraction ---
                        else:
                            # Look for meta.profile for referenced profiles -> add profile type
                            profiles = data.get('meta', {}).get('profile', [])
                            for profile_uri in profiles:
                                 if profile_uri:
                                      profile_type = profile_uri.split('/')[-1]
                                      if profile_type and profile_type[0].isupper(): used_types.add(profile_type)

                            # ValueSet: Check compose.include.system (often points to CodeSystem)
                            if resource_type == 'ValueSet':
                                for include in data.get('compose', {}).get('include', []):
                                    system = include.get('system')
                                    # Heuristic: If it looks like a FHIR core codesystem URL, extract type
                                    if system and system.startswith('http://hl7.org/fhir/'):
                                        type_name = system.split('/')[-1]
                                        # Check if it looks like a ResourceType
                                        if type_name and type_name[0].isupper() and not type_name.startswith('sid'): # Avoid things like sid/us-ssn
                                             used_types.add(type_name)
                                    # Could add more heuristics for other terminology servers

                            # CapabilityStatement: Check rest.resource.type and rest.resource.profile
                            if resource_type == 'CapabilityStatement':
                                 for rest_item in data.get('rest', []):
                                      for resource_item in rest_item.get('resource', []):
                                           res_type = resource_item.get('type')
                                           if res_type and res_type[0].isupper(): used_types.add(res_type)
                                           profile_uri = resource_item.get('profile')
                                           if profile_uri:
                                                profile_type = profile_uri.split('/')[-1]
                                                if profile_type and profile_type[0].isupper(): used_types.add(profile_type)


                            # --- Generic recursive search for 'reference' fields? ---
                            # This could be expensive. Let's rely on SDs for now.
                            # def find_references(obj):
                            #     if isinstance(obj, dict):
                            #         for k, v in obj.items():
                            #             if k == 'reference' and isinstance(v, str):
                            #                 ref_type = v.split('/')[0]
                            #                 if ref_type and ref_type[0].isupper(): used_types.add(ref_type)
                            #             else:
                            #                 find_references(v)
                            #     elif isinstance(obj, list):
                            #         for item in obj:
                            #             find_references(item)
                            # find_references(data)

                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse JSON in {member.name} for used types: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode {member.name} for used types: {e}")
                except Exception as e:
                    logger.warning(f"Could not process member {member.name} for used types: {e}")
                finally:
                    if fileobj:
                        fileobj.close()

    except tarfile.ReadError as e:
         logger.error(f"Tar ReadError extracting used types from {tgz_path}: {e}")
    except tarfile.TarError as e:
         logger.error(f"TarError extracting used types from {tgz_path}: {e}")
    except FileNotFoundError:
         logger.error(f"Package file not found for used type extraction: {tgz_path}")
    except Exception as e:
        logger.error(f"Error extracting used types from {tgz_path}: {e}", exc_info=True)

    # Filter out potential primitives or base types that aren't resources?
    # E.g., 'string', 'boolean', 'Element', 'BackboneElement', 'Resource'
    core_non_resource_types = {'string', 'boolean', 'integer', 'decimal', 'uri', 'url', 'canonical',
                               'base64Binary', 'instant', 'date', 'dateTime', 'time', 'code', 'oid', 'id',
                               'markdown', 'unsignedInt', 'positiveInt', 'xhtml',
                               'Element', 'BackboneElement', 'Resource', 'DomainResource', 'DataType'}
    final_used_types = {t for t in used_types if t not in core_non_resource_types and t[0].isupper()}

    logger.debug(f"Extracted used types from {os.path.basename(tgz_path)}: {final_used_types}")
    return final_used_types


def map_types_to_packages(used_types, all_dependencies):
    """Maps used types to the packages that provide them based on dependency lists."""
    type_to_package = {}
    processed_types = set()

    # Pass 1: Exact matches in dependencies
    for (pkg_name, pkg_version), deps in all_dependencies.items():
        for dep_name, dep_version in deps.items():
             # Simple heuristic: if type name is in dependency package name
             # This is weak, needs improvement. Ideally, packages declare exported types.
             for t in used_types:
                  # Exact match or common pattern (e.g., USCorePatient -> us.core)
                  # Need a better mapping strategy - this is very basic.
                  # Example: If 'USCorePatient' is used, and 'us.core' is a dependency.
                  # A more robust approach would involve loading the .index.json from dependency packages.
                  # For now, let's just use a simplified direct check:
                  # If a dependency name contains the type name (lowercase)
                  if t not in type_to_package and t.lower() in dep_name.lower():
                       type_to_package[t] = (dep_name, dep_version)
                       processed_types.add(t)
                       logger.debug(f"Mapped type '{t}' to dependency package '{dep_name}' based on name heuristic.")

    # Pass 2: Check the package itself
    for (pkg_name, pkg_version), deps in all_dependencies.items():
         for t in used_types:
              if t not in type_to_package and t.lower() in pkg_name.lower():
                   type_to_package[t] = (pkg_name, pkg_version)
                   processed_types.add(t)
                   logger.debug(f"Mapped type '{t}' to source package '{pkg_name}' based on name heuristic.")


    # Fallback: map remaining types to the canonical package if not already mapped
    canonical_name, canonical_version = CANONICAL_PACKAGE
    unmapped_types = used_types - processed_types
    if unmapped_types:
         logger.info(f"Using canonical package {canonical_name}#{canonical_version} as fallback for unmapped types: {unmapped_types}")
         for t in unmapped_types:
             type_to_package[t] = CANONICAL_PACKAGE

    logger.debug(f"Final type-to-package mapping: {type_to_package}")
    return type_to_package

def import_package_and_dependencies(initial_name, initial_version, dependency_mode='recursive'):
    """Orchestrates recursive download and dependency extraction based on the dependency mode."""
    logger.info(f"Starting import for {initial_name}#{initial_version} with dependency_mode={dependency_mode}")
    results = {
        'requested': (initial_name, initial_version),
        'processed': set(),       # Tuples (name, version) successfully processed (downloaded + deps extracted)
        'downloaded': {},         # Dict {(name, version): save_path} for successfully downloaded
        'all_dependencies': {}, # Dict {(name, version): {dep_name: dep_ver}} stores extracted deps for each processed pkg
        'dependencies': [],       # List of unique {"name": X, "version": Y} across all processed packages
        'errors': []              # List of error messages encountered
    }
    # Queue stores (name, version) tuples to process
    pending_queue = [(initial_name, initial_version)]
    # Lookup stores (name, version) tuples that have been added to queue or processed, prevents cycles/re-queuing
    queued_or_processed_lookup = set([(initial_name, initial_version)])
    all_found_dependencies = set() # Store unique dep tuples {(name, version)} found


    # --- Main Processing Loop ---
    while pending_queue:
        name, version = pending_queue.pop(0)
        package_id_tuple = (name, version)

        # Already successfully processed? Skip. (Shouldn't happen with lookup check before queueing, but safety)
        if package_id_tuple in results['processed']:
            logger.debug(f"Skipping already processed package: {name}#{version}")
            continue

        logger.info(f"Processing package from queue: {name}#{version}")

        # --- Download ---
        save_path, dl_error = download_package(name, version)
        if dl_error:
            error_msg = f"Download failed for {name}#{version}: {dl_error}"
            results['errors'].append(error_msg)
            logger.error(error_msg)
            # Do not add to processed, leave in lookup to prevent re-queueing a known failure
            continue # Move to next item in queue
        else:
            results['downloaded'][package_id_tuple] = save_path
            logger.info(f"Successfully downloaded/verified {name}#{version} at {save_path}")

        # --- Extract Dependencies ---
        dependencies, dep_error = extract_dependencies(save_path)
        if dep_error:
            # Log error but potentially continue processing other packages if deps are just missing
            error_msg = f"Dependency extraction failed for {name}#{version}: {dep_error}"
            results['errors'].append(error_msg)
            logger.error(error_msg)
            # Mark as processed even if dep extraction fails, as download succeeded
            results['processed'].add(package_id_tuple)
            # Don't queue dependencies if extraction failed
            continue
        elif dependencies is None:
            # This indicates a more severe error during extraction (e.g., corrupted tar)
            error_msg = f"Dependency extraction returned critical error for {name}#{version}. Aborting dependency processing for this package."
            results['errors'].append(error_msg)
            logger.error(error_msg)
            results['processed'].add(package_id_tuple) # Mark processed
            continue


        # Store extracted dependencies for this package
        results['all_dependencies'][package_id_tuple] = dependencies
        results['processed'].add(package_id_tuple) # Mark as successfully processed
        logger.debug(f"Successfully processed {name}#{version}. Dependencies found: {list(dependencies.keys())}")

        # Add unique dependencies to the overall list and potentially the queue
        current_package_deps = []
        for dep_name, dep_version in dependencies.items():
            if isinstance(dep_name, str) and isinstance(dep_version, str) and dep_name and dep_version:
                dep_tuple = (dep_name, dep_version)
                current_package_deps.append({"name": dep_name, "version": dep_version}) # For metadata
                if dep_tuple not in all_found_dependencies:
                     all_found_dependencies.add(dep_tuple)
                     results['dependencies'].append({"name": dep_name, "version": dep_version}) # Add to overall unique list

                # --- Queue Dependencies Based on Mode ---
                # Check if not already queued or processed
                if dep_tuple not in queued_or_processed_lookup:
                    should_queue = False
                    if dependency_mode == 'recursive':
                         should_queue = True
                    elif dependency_mode == 'patch-canonical' and dep_tuple == CANONICAL_PACKAGE:
                         should_queue = True
                    elif dependency_mode == 'tree-shaking':
                         # Tree shaking requires calculating used types *after* initial pkg is processed
                         # This logic needs adjustment - calculate used types only once for the root package.
                         # Let's defer full tree-shaking queuing logic for now, treat as 'none'.
                         # TODO: Implement tree-shaking queuing properly outside the loop based on initial package's used types.
                         pass

                    if should_queue:
                         logger.debug(f"Adding dependency to queue ({dependency_mode}): {dep_name}#{dep_version}")
                         pending_queue.append(dep_tuple)
                         queued_or_processed_lookup.add(dep_tuple)
            else:
                 logger.warning(f"Skipping invalid dependency entry in {name}#{version}: name='{dep_name}', version='{dep_version}'")

        # --- Save Metadata (after successful download and dep extraction) ---
        # We need profile relationship info which comes from process_package_file
        # Let's call it here if needed for metadata, though it duplicates effort if called later.
        # Alternative: Save basic metadata first, update later?
        # Let's just save what we have now. Profile relations can be added by a separate process.
        save_package_metadata(name, version, dependency_mode, current_package_deps)
        # TODO: Rework metadata saving if compliesWith/imposedBy is needed during import.


    # --- Post-Loop Processing (e.g., for Tree Shaking) ---
    if dependency_mode == 'tree-shaking' and (initial_name, initial_version) in results['downloaded']:
         logger.info("Performing tree-shaking dependency analysis...")
         root_save_path = results['downloaded'][(initial_name, initial_version)]
         used_types = extract_used_types(root_save_path)
         if used_types:
              type_to_package = map_types_to_packages(used_types, results['all_dependencies'])
              logger.debug(f"Tree-shaking mapping: {type_to_package}")
              tree_shaken_deps_to_ensure = set(type_to_package.values())

              # Ensure canonical package is included if tree-shaking mode implies it
              if CANONICAL_PACKAGE not in tree_shaken_deps_to_ensure:
                   logger.debug(f"Adding canonical package {CANONICAL_PACKAGE} to tree-shaking set.")
                   tree_shaken_deps_to_ensure.add(CANONICAL_PACKAGE)

              initial_package_tuple = (initial_name, initial_version)
              if initial_package_tuple in tree_shaken_deps_to_ensure:
                   tree_shaken_deps_to_ensure.remove(initial_package_tuple) # Don't queue self

              additional_processing_needed = False
              for dep_tuple in tree_shaken_deps_to_ensure:
                   if dep_tuple not in results['processed'] and dep_tuple not in queued_or_processed_lookup:
                        logger.info(f"Queueing missing tree-shaken dependency: {dep_tuple[0]}#{dep_tuple[1]}")
                        pending_queue.append(dep_tuple)
                        queued_or_processed_lookup.add(dep_tuple)
                        additional_processing_needed = True

              # If tree-shaking added new packages, re-run the processing loop
              if additional_processing_needed:
                   logger.info("Re-running processing loop for tree-shaken dependencies...")
                   # This recursive call structure isn't ideal, better to refactor loop.
                   # For now, let's just run the loop again conceptually.
                   # This requires refactoring the main loop logic to be callable.
                   # --- TEMPORARY WORKAROUND: Just log and state limitation ---
                   logger.warning("Tree-shaking identified additional dependencies. Manual re-run or refactoring needed to process them.")
                   results['errors'].append("Tree-shaking identified further dependencies; re-run required for full processing.")
                   # TODO: Refactor the while loop into a callable function to handle recursive/iterative processing.

    proc_count = len(results['processed'])
    dl_count = len(results['downloaded'])
    err_count = len(results['errors'])
    logger.info(f"Import finished for {initial_name}#{initial_version}. Processed: {proc_count}, Downloaded: {dl_count}, Errors: {err_count}")
    # Make sure unique list of deps is accurate
    results['dependencies'] = [ {"name": d[0], "version": d[1]} for d in all_found_dependencies]
    return results



def process_package_file(tgz_path):
    """Extracts types, profile status, MS elements, examples, and profile relationships from a downloaded .tgz package."""
    logger.info(f"Processing package file details: {tgz_path}")

    results = {
        'resource_types_info': [],      # List of dicts about each Resource/Profile
        'must_support_elements': {},    # Dict: { 'ResourceName/ProfileId': ['path1', 'path2'] }
        'examples': {},                 # Dict: { 'ResourceName/ProfileId': ['example_path1'] }
        'complies_with_profiles': [],   # List of canonical URLs
        'imposed_profiles': [],         # List of canonical URLs
        'errors': []
    }
    # Use defaultdict for easier aggregation
    # Key: SD ID if profile, otherwise ResourceType. Value: dict with info.
    resource_info = defaultdict(lambda: {
        'name': None,       # The key (SD ID or ResourceType)
        'type': None,       # Base FHIR type (e.g., Patient)
        'is_profile': False,
        'ms_flag': False,   # Does this SD define *any* MS elements?
        'ms_paths': set(),  # Specific MS element paths defined *in this SD*
        'examples': set(),  # Paths to example files linked to this type/profile
        'sd_processed': False # Flag to avoid reprocessing MS flags for the same SD key
    })

    if not tgz_path or not os.path.exists(tgz_path):
        results['errors'].append(f"Package file not found: {tgz_path}")
        logger.error(f"Package file not found during processing: {tgz_path}")
        return results

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            members = tar.getmembers() # Get all members once
            logger.debug(f"Found {len(members)} members in {os.path.basename(tgz_path)}")

            # --- Pass 1: Process StructureDefinitions ---
            logger.debug("Processing StructureDefinitions...")
            for member in members:
                # Basic filtering
                if not member.isfile() or not member.name.startswith('package/') or not member.name.lower().endswith('.json'):
                    continue
                base_filename_lower = os.path.basename(member.name).lower()
                if base_filename_lower in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue

                fileobj = None
                try:
                    fileobj = tar.extractfile(member)
                    if not fileobj: continue

                    content_bytes = fileobj.read()
                    content_string = content_bytes.decode('utf-8-sig')
                    data = json.loads(content_string)

                    if not isinstance(data, dict) or data.get('resourceType') != 'StructureDefinition':
                        continue # Only interested in SDs in this pass

                    # --- Process the StructureDefinition ---
                    profile_id = data.get('id') or data.get('name') # Use ID, fallback to name
                    sd_type = data.get('type') # The base FHIR type (e.g., Patient)
                    sd_base = data.get('baseDefinition')
                    is_profile_sd = bool(sd_base) # It's a profile if it has a baseDefinition

                    if not profile_id:
                        logger.warning(f"StructureDefinition in {member.name} missing 'id' and 'name', skipping.")
                        continue
                    if not sd_type:
                        logger.warning(f"StructureDefinition '{profile_id}' in {member.name} missing 'type', skipping.")
                        continue

                    entry_key = profile_id # Use the SD's ID as the key
                    entry = resource_info[entry_key]

                    # Only process once per entry_key
                    if entry.get('sd_processed'): continue

                    entry['name'] = entry_key
                    entry['type'] = sd_type
                    entry['is_profile'] = is_profile_sd

                    # Extract compliesWithProfile and imposeProfile extensions
                    complies_with = []
                    imposed = []
                    for ext in data.get('extension', []):
                        ext_url = ext.get('url')
                        value = ext.get('valueCanonical')
                        if value:
                             if ext_url == 'http://hl7.org/fhir/StructureDefinition/structuredefinition-compliesWithProfile':
                                 complies_with.append(value)
                             elif ext_url == 'http://hl7.org/fhir/StructureDefinition/structuredefinition-imposeProfile':
                                 imposed.append(value)

                    # Add to overall results (unique)
                    results['complies_with_profiles'].extend(c for c in complies_with if c not in results['complies_with_profiles'])
                    results['imposed_profiles'].extend(i for i in imposed if i not in results['imposed_profiles'])

                    # Find Must Support elements defined *in this specific SD*
                    has_ms_in_this_sd = False
                    ms_paths_in_this_sd = set()
                    # Check differential first, then snapshot if needed? Or combine? Let's combine.
                    elements = data.get('snapshot', {}).get('element', []) + data.get('differential', {}).get('element', [])
                    # De-duplicate elements based on path if combining snapshot and differential (though usually only one is primary)
                    processed_element_paths = set()
                    unique_elements = []
                    for el in elements:
                         el_path = el.get('path')
                         if el_path and el_path not in processed_element_paths:
                              unique_elements.append(el)
                              processed_element_paths.add(el_path)
                         elif not el_path: # Include elements without paths? Maybe not.
                              pass

                    for element in unique_elements:
                        if isinstance(element, dict) and element.get('mustSupport') is True:
                            element_path = element.get('path')
                            if element_path:
                                ms_paths_in_this_sd.add(element_path)
                                has_ms_in_this_sd = True
                            else:
                                logger.warning(f"Found mustSupport=true without path in element of {entry_key} ({member.name})")

                    if ms_paths_in_this_sd:
                        entry['ms_paths'] = ms_paths_in_this_sd
                        entry['ms_flag'] = True # Set flag if this SD defines MS elements
                        logger.debug(f"Found {len(ms_paths_in_this_sd)} MS elements defined in SD {entry_key}")

                    entry['sd_processed'] = True # Mark this SD as processed

                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse JSON SD in {member.name}: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode SD in {member.name}: {e}")
                except Exception as e:
                    logger.warning(f"Could not process SD member {member.name}: {e}", exc_info=False) # Keep log cleaner
                finally:
                    if fileobj: fileobj.close()

            # --- Pass 2: Process Examples ---
            logger.debug("Processing Examples...")
            for member in members:
                 # Basic filtering
                if not member.isfile() or not member.name.startswith('package/'): # Allow non-JSON examples too
                    continue
                member_name_lower = member.name.lower()
                base_filename_lower = os.path.basename(member_name_lower)
                if base_filename_lower in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue

                # Heuristic for identifying examples
                # Check directory name or filename conventions
                is_example = 'example' in member.name.split('/') or 'example' in base_filename_lower.split('-') or 'example' in base_filename_lower.split('.')

                if not is_example: continue

                logger.debug(f"Processing potential example file: {member.name}")
                is_json = member_name_lower.endswith('.json')
                fileobj = None
                associated_key = None

                try:
                    if is_json:
                        fileobj = tar.extractfile(member)
                        if not fileobj: continue
                        content_bytes = fileobj.read()
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)

                        if not isinstance(data, dict): continue
                        resource_type = data.get('resourceType')
                        if not resource_type: continue

                        # Try to associate example with a profile using meta.profile
                        profile_meta = data.get('meta', {}).get('profile', [])
                        found_profile_match = False
                        if profile_meta and isinstance(profile_meta, list):
                            for profile_url in profile_meta:
                                # Extract profile ID from canonical URL
                                profile_id_from_meta = profile_url.split('/')[-1]
                                if profile_id_from_meta in resource_info:
                                    associated_key = profile_id_from_meta
                                    found_profile_match = True
                                    logger.debug(f"Example {member.name} associated with profile {associated_key} via meta.profile")
                                    break # Use first match

                        # If no profile match, associate with the base resource type SD (if any)
                        if not found_profile_match:
                             # Find SD where type matches the example's resourceType and is_profile is False
                             matching_base_sd_keys = [k for k, v in resource_info.items() if v.get('type') == resource_type and not v.get('is_profile') and v.get('sd_processed')]
                             if matching_base_sd_keys:
                                  associated_key = matching_base_sd_keys[0] # Use the first matching base SD key
                                  logger.debug(f"Example {member.name} associated with base type SD {associated_key}")
                             else:
                                  # Fallback: If no SD processed for this base type yet, use the type itself as key
                                  associated_key = resource_type
                                  logger.debug(f"Example {member.name} associated with resource type {associated_key} (no specific SD found/processed yet)")

                    else:
                         # For non-JSON examples, try to guess based on filename
                         # e.g., patient-example.xml -> Patient
                         # e.g., us-core-patient-example.xml -> us-core-patient (if profile exists)
                         guessed_profile_id = None
                         if '-' in base_filename_lower:
                              # Try matching parts against known profile IDs
                              parts = base_filename_lower.split('-')
                              potential_id = parts[0]
                              if potential_id in resource_info:
                                   guessed_profile_id = potential_id
                              else: # Try combining parts? e.g., us-core
                                   if len(parts) > 1:
                                        potential_id_2 = f"{parts[0]}-{parts[1]}"
                                        if potential_id_2 in resource_info:
                                             guessed_profile_id = potential_id_2

                         if guessed_profile_id:
                              associated_key = guessed_profile_id
                              logger.debug(f"Non-JSON Example {member.name} associated with profile {associated_key} via filename heuristic")
                         else:
                              # Fallback to guessing base type
                              guessed_type = base_filename_lower.split('-')[0].split('.')[0].capitalize()
                              matching_base_sd_keys = [k for k, v in resource_info.items() if v.get('type') == guessed_type and not v.get('is_profile') and v.get('sd_processed')]
                              if matching_base_sd_keys:
                                  associated_key = matching_base_sd_keys[0]
                                  logger.debug(f"Non-JSON Example {member.name} associated with base type SD {associated_key} via filename heuristic")
                              elif guessed_type:
                                  associated_key = guessed_type
                                  logger.debug(f"Non-JSON Example {member.name} associated with resource type {associated_key} via filename heuristic (no specific SD found/processed yet)")


                    # Add example path to the associated resource/profile info
                    if associated_key:
                         # Ensure the entry exists even if no SD was processed (for base types)
                         if associated_key not in resource_info:
                              resource_info[associated_key]['name'] = associated_key
                              # Try to infer type if possible (might be None)
                              resource_info[associated_key]['type'] = data.get('resourceType') if is_json else associated_key

                         resource_info[associated_key]['examples'].add(member.name)
                    else:
                         logger.warning(f"Could not associate example {member.name} with any known resource or profile.")


                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse JSON example in {member.name}: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode example in {member.name}: {e}")
                except Exception as e:
                    logger.warning(f"Could not process example member {member.name}: {e}", exc_info=False)
                finally:
                    if fileobj: fileobj.close()


        # --- Final Formatting ---
        final_list = []
        final_ms_elements = {}
        final_examples = {}
        logger.debug(f"Finalizing results from resource_info keys: {list(resource_info.keys())}")

        # Make sure all base resource types mentioned (even without explicit SDs) are included
        all_types_mentioned = set(v['type'] for v in resource_info.values() if v.get('type'))
        for type_name in all_types_mentioned:
             if type_name not in resource_info:
                  # Add a basic entry if a type was mentioned (e.g., by an example) but had no SD
                  if type_name and type_name[0].isupper(): # Basic check it looks like a resource type
                      logger.debug(f"Adding basic entry for resource type '{type_name}' mentioned but without processed SD.")
                      resource_info[type_name]['name'] = type_name
                      resource_info[type_name]['type'] = type_name
                      resource_info[type_name]['is_profile'] = False


        for key, info in resource_info.items():
            display_name = info.get('name') or key
            base_type = info.get('type')

            # Skip if essential info is missing (shouldn't happen with defaultdict + population)
            if not display_name or not base_type:
                 logger.warning(f"Skipping formatting for incomplete key: {key} - Info: {info}")
                 continue

            logger.debug(f"Formatting item '{display_name}': type='{base_type}', profile='{info.get('is_profile', False)}', ms_flag='{info.get('ms_flag', False)}'")
            final_list.append({
                'name': display_name, # This is the SD ID or ResourceType
                'type': base_type,    # The base FHIR resource type
                'is_profile': info.get('is_profile', False),
                'must_support': info.get('ms_flag', False) # Does this SD *define* MS elements?
            })
            if info['ms_paths']:
                final_ms_elements[display_name] = sorted(list(info['ms_paths']))
            if info['examples']:
                final_examples[display_name] = sorted(list(info['examples']))


        # Sort profiles after base types, then alphabetically
        results['resource_types_info'] = sorted(final_list, key=lambda x: (x.get('is_profile', False), x.get('name', '')))
        results['must_support_elements'] = final_ms_elements
        results['examples'] = final_examples
        # Ensure relationship lists are unique (done during addition now)
        # results['complies_with_profiles'] = sorted(list(set(results['complies_with_profiles'])))
        # results['imposed_profiles'] = sorted(list(set(results['imposed_profiles'])))

    except tarfile.ReadError as e:
        err_msg = f"Tar ReadError processing package file {tgz_path}: {e}"
        logger.error(err_msg)
        results['errors'].append(err_msg)
    except tarfile.TarError as e:
        err_msg = f"TarError processing package file {tgz_path}: {e}"
        logger.error(err_msg)
        results['errors'].append(err_msg)
    except FileNotFoundError:
         err_msg = f"Package file not found during processing: {tgz_path}"
         logger.error(err_msg)
         results['errors'].append(err_msg)
    except Exception as e:
        err_msg = f"Unexpected error processing package file {tgz_path}: {e}"
        logger.error(err_msg, exc_info=True)
        results['errors'].append(err_msg)

    # Logging counts
    final_types_count = len(results['resource_types_info'])
    ms_defining_count = sum(1 for r in results['resource_types_info'] if r['must_support']) # Count SDs defining MS
    total_ms_paths = sum(len(v) for v in results['must_support_elements'].values())
    total_examples = sum(len(v) for v in results['examples'].values())
    logger.info(f"Package processing finished for {os.path.basename(tgz_path)}: "
                f"{final_types_count} Resources/Profiles identified; "
                f"{ms_defining_count} define MS elements ({total_ms_paths} total MS paths); "
                f"{total_examples} examples found. "
                f"CompliesWith: {len(results['complies_with_profiles'])}, Imposed: {len(results['imposed_profiles'])}")

    return results

# --- Example Usage (if running script directly) ---
if __name__ == '__main__':
    # Configure logger for direct script execution
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.info("Running services.py directly for testing.")

    # Mock Flask app context minimally for config/instance path
    class MockFlaskConfig(dict):
        pass
    class MockFlaskCurrentApp:
        config = MockFlaskConfig()
        # Calculate instance path relative to this file
        instance_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'instance'))
    # Need to manually set current_app for testing outside Flask request context
    # This is tricky. Let's bypass current_app dependency in _get_download_dir for direct testing.
    # OR, provide a mock. Best approach is to structure code to reduce Flask dependency in core logic.

    # For testing, let's override _get_download_dir or manually create the dir
    test_download_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'instance', DOWNLOAD_DIR_NAME))
    os.makedirs(test_download_dir, exist_ok=True)
    logger.info(f"Using test download directory: {test_download_dir}")

    # Override the helper function for testing context
    original_get_download_dir = _get_download_dir
    def mock_get_download_dir():
         # In test, don't rely on current_app if possible
         # Ensure config exists if needed by validation code
         if not hasattr(mock_get_download_dir, 'config'):
              mock_get_download_dir.config = {'FHIR_PACKAGES_DIR': test_download_dir}
         return test_download_dir
    _get_download_dir = mock_get_download_dir
    # Add the FHIR_PACKAGES_DIR to the mock config directly
    _get_download_dir.config = {'FHIR_PACKAGES_DIR': test_download_dir}

    # --- Test Case 1: Import AU Core Patient Package ---
    pkg_name = "hl7.fhir.au.core"
    pkg_version = "1.0.1" # Use a specific version known to exist
    logger.info(f"\n--- Testing Import: {pkg_name}#{pkg_version} ---")
    import_results = import_package_and_dependencies(pkg_name, pkg_version, dependency_mode='recursive')
    # print("Import Results:", json.dumps(import_results, default=lambda o: '<not serializable>', indent=2))
    if not import_results['errors'] and (pkg_name, pkg_version) in import_results['downloaded']:
         logger.info(f"Import successful for {pkg_name}#{pkg_version}")

         # --- Test Case 2: Validate Patient Resource ---
         logger.info(f"\n--- Testing Validation: Patient Example ---")
         patient_resource = {
              "resourceType": "Patient",
              "id": "banks-mia-leanne",
              "meta": { "profile": ["http://hl7.org.au/fhir/core/StructureDefinition/au-core-patient"] },
              "identifier": [{
                  "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "NI"}], "text": "IHI"},
                  "system": "http://ns.electronichealth.net.au/id/hi/ihi/1.0",
                  "value": "8003608333647261"
              }],
              "name": [{"use": "usual", "family": "Banks", "given": ["Mia", "Leanne"]}],
              "telecom": [{"system": "phone", "value": "0491574632", "use": "mobile"}],
              "gender": "female",
              "birthDate": "1983-08-25",
              "address": [{"line": ["50 Sebastien St"], "city": "Minjary", "state": "NSW", "postalCode": "2720", "country": "AU"}]
              # Missing communication on purpose to test warnings/errors if required by profile
         }
         validation_result = validate_resource_against_profile(pkg_name, pkg_version, patient_resource)
         print("\nPatient Validation Result:")
         print(json.dumps(validation_result, indent=2))

         # --- Test Case 3: Validate Allergy Resource ---
         logger.info(f"\n--- Testing Validation: Allergy Example ---")
         allergy_resource = {
              "resourceType": "AllergyIntolerance",
              "id": "lactose",
              "meta": {"profile": ["http://hl7.org.au/fhir/core/StructureDefinition/au-core-allergyintolerance"]},
              "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
              "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "code": "confirmed"}]},
              "code": {"coding": [{"system": "http://snomed.info/sct", "code": "782415009", "display": "Intolerance to lactose"}]},
              "patient": {"reference": "Patient/banks-mia-leanne"},
              "onsetDateTime": "2022", # Example of choice type
              "reaction": [{
                  "manifestation": [{"coding": [{"system": "http://snomed.info/sct", "code": "21522001", "display": "Abdominal pain"}]}],
                  "severity": "mild"
              }]
         }
         validation_result_allergy = validate_resource_against_profile(pkg_name, pkg_version, allergy_resource)
         print("\nAllergy Validation Result:")
         print(json.dumps(validation_result_allergy, indent=2))

    else:
         logger.error(f"Import failed for {pkg_name}#{pkg_version}, cannot proceed with validation tests.")
         print("Import Errors:", import_results['errors'])

    # Restore original function if necessary
    _get_download_dir = original_get_download_dir