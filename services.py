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

# --- Helper Functions ---

def _get_download_dir():
    """Gets the absolute path to the download directory, creating it if needed."""
    logger = logging.getLogger(__name__)
    instance_path = None # Initialize
    try:
        # --- FIX: Indent code inside try block ---
        instance_path = current_app.instance_path
        logger.debug(f"Using instance path from current_app: {instance_path}")
    except RuntimeError:
        # --- FIX: Indent code inside except block ---
        logger.warning("No app context for instance_path, constructing relative path.")
        instance_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'instance'))
        logger.debug(f"Constructed instance path: {instance_path}")

    # This part depends on instance_path being set above
    if not instance_path:
         logger.error("Fatal Error: Could not determine instance path.")
         return None

    download_dir = os.path.join(instance_path, DOWNLOAD_DIR_NAME)
    try:
        # --- FIX: Indent code inside try block ---
        os.makedirs(download_dir, exist_ok=True)
        return download_dir
    except OSError as e:
        # --- FIX: Indent code inside except block ---
        logger.error(f"Fatal Error creating dir {download_dir}: {e}", exc_info=True)
        return None

def sanitize_filename_part(text): # Public version
    """Basic sanitization for name/version parts of filename."""
    # --- FIX: Indent function body ---
    safe_text = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in text)
    safe_text = re.sub(r'_+', '_', safe_text) # Uses re
    safe_text = safe_text.strip('_-.')
    return safe_text if safe_text else "invalid_name"

def _construct_tgz_filename(name, version):
    """Constructs the standard filename using the sanitized parts."""
    # --- FIX: Indent function body ---
    return f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.tgz"

def find_and_extract_sd(tgz_path, resource_identifier): # Public version
    """Helper to find and extract SD json from a given tgz path by ID, Name, or Type."""
    # --- FIX: Ensure consistent indentation ---
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
                                 break # Stop searching once found
                except Exception as e:
                    # Log issues reading/parsing individual files but continue search
                    logger.warning(f"Could not read/parse potential SD {member.name}: {e}")
                finally:
                    if fileobj: fileobj.close() # Ensure resource cleanup

            if sd_data is None:
                logger.warning(f"SD matching '{resource_identifier}' not found within archive {os.path.basename(tgz_path)}")
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

# --- Core Service Functions ---

def download_package(name, version):
    """ Downloads a single FHIR package. Returns (save_path, error_message) """
    # --- FIX: Ensure consistent indentation ---
    logger = logging.getLogger(__name__)
    download_dir = _get_download_dir()
    if not download_dir:
        return None, "Could not get/create download directory."

    package_id = f"{name}#{version}"
    package_url = f"{FHIR_REGISTRY_BASE_URL}/{name}/{version}"
    filename = _construct_tgz_filename(name, version) # Uses public sanitize via helper
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
    # --- FIX: Ensure consistent indentation ---
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
                    package_data = json.loads(package_json_fileobj.read().decode('utf-8-sig'))
                    dependencies = package_data.get('dependencies', {})
                finally:
                    package_json_fileobj.close()
            else:
                raise FileNotFoundError(f"Could not extract {package_json_path}")
    except KeyError:
        error_message = f"'{package_json_path}' not found in {os.path.basename(tgz_path)}.";
        logger.warning(error_message) # OK if missing
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        error_message = f"Parse error in {package_json_path}: {e}"; logger.error(error_message); dependencies = None # Parsing failed
    except (tarfile.TarError, FileNotFoundError) as e:
        error_message = f"Archive error {os.path.basename(tgz_path)}: {e}"; logger.error(error_message); dependencies = None # Archive read failed
    except Exception as e:
        error_message = f"Unexpected error extracting deps: {e}"; logger.error(error_message, exc_info=True); dependencies = None
    return dependencies, error_message


# --- Recursive Import Orchestrator ---
def import_package_and_dependencies(initial_name, initial_version):
    """Orchestrates recursive download and dependency extraction."""
    # --- FIX: Ensure consistent indentation ---
    logger = logging.getLogger(__name__)
    logger.info(f"Starting recursive import for {initial_name}#{initial_version}")
    results = {
        'requested': (initial_name, initial_version),
        'processed': set(),
        'downloaded': {},
        'all_dependencies': {},
        'dependencies': [],  # New field to store dependencies as a list
        'errors': []
    }
    pending_queue = [(initial_name, initial_version)]
    processed_lookup = set()

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
            if package_id_tuple == results['requested']:
                 logger.error("Aborting import: Initial package download failed.")
                 break
            else:
                 continue
        else: # Download OK
            results['downloaded'][package_id_tuple] = save_path
            # --- Correctly indented block ---
            dependencies, dep_error = extract_dependencies(save_path)
            if dep_error:
                results['errors'].append(f"Dependency extraction failed for {name}#{version}: {dep_error}")
            elif dependencies is not None:
                results['all_dependencies'][package_id_tuple] = dependencies
                results['processed'].add(package_id_tuple)
                logger.debug(f"Dependencies for {name}#{version}: {list(dependencies.keys())}")
                # Add dependencies to the new 'dependencies' list
                for dep_name, dep_version in dependencies.items():
                    if isinstance(dep_name, str) and isinstance(dep_version, str) and dep_name and dep_version:
                        dep_tuple = (dep_name, dep_version)
                        results['dependencies'].append({"name": dep_name, "version": dep_version})
                        if dep_tuple not in processed_lookup:
                            if dep_tuple not in pending_queue:
                                pending_queue.append(dep_tuple)
                                logger.debug(f"Added to queue: {dep_name}#{dep_version}")
                    else:
                        logger.warning(f"Skipping invalid dependency '{dep_name}': '{dep_version}' in {name}#{version}")
            # --- End Correctly indented block ---

    proc_count=len(results['processed']); dl_count=len(results['downloaded']); err_count=len(results['errors'])
    logger.info(f"Import finished. Processed: {proc_count}, Downloaded/Verified: {dl_count}, Errors: {err_count}")
    return results


# --- Package File Content Processor (V6.2 - Fixed MS path handling) ---
def process_package_file(tgz_path):
    """ Extracts types, profile status, MS elements, and examples from a downloaded .tgz package (Single Pass). """
    logger = logging.getLogger(__name__)
    logger.info(f"Processing package file details (V6.2 Logic): {tgz_path}")

    results = {'resource_types_info': [], 'must_support_elements': {}, 'examples': {}, 'errors': [] }
    resource_info = defaultdict(lambda: {'name': None, 'type': None, 'is_profile': False, 'ms_flag': False, 'ms_paths': set(), 'examples': set()})

    if not tgz_path or not os.path.exists(tgz_path):
        results['errors'].append(f"Package file not found: {tgz_path}"); return results

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar:
                if not member.isfile() or not member.name.startswith('package/') or not member.name.lower().endswith(('.json', '.xml', '.html')): continue
                member_name_lower = member.name.lower(); base_filename_lower = os.path.basename(member_name_lower); fileobj = None
                if base_filename_lower in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']: continue

                is_example = member.name.startswith('package/example/') or 'example' in base_filename_lower
                is_json = member_name_lower.endswith('.json')

                try: # Process individual member
                    if is_json:
                        fileobj = tar.extractfile(member);
                        if not fileobj: continue
                        content_bytes = fileobj.read(); content_string = content_bytes.decode('utf-8-sig'); data = json.loads(content_string)
                        if not isinstance(data, dict) or 'resourceType' not in data: continue

                        resource_type = data['resourceType']; entry_key = resource_type; is_sd = False

                        if resource_type == 'StructureDefinition':
                            is_sd = True; profile_id = data.get('id') or data.get('name'); sd_type = data.get('type'); sd_base = data.get('baseDefinition'); is_profile_sd = bool(sd_base);
                            if not profile_id or not sd_type: logger.warning(f"SD missing ID or Type: {member.name}"); continue
                            entry_key = profile_id
                        
                        entry = resource_info[entry_key]; entry.setdefault('type', resource_type) # Ensure type exists

                        if is_sd:
                            entry['name'] = entry_key; entry['type'] = sd_type; entry['is_profile'] = is_profile_sd;
                            if not entry.get('sd_processed'):
                                has_ms = False; ms_paths_for_sd = set()
                                for element_list in [data.get('snapshot', {}).get('element', []), data.get('differential', {}).get('element', [])]:
                                    for element in element_list:
                                        if isinstance(element, dict) and element.get('mustSupport') is True:
                                            # --- FIX: Check path safely ---
                                            element_path = element.get('path') 
                                            if element_path: # Only add if path exists
                                                ms_paths_for_sd.add(element_path)
                                                has_ms = True # Mark MS found if we added a path
                                            else:
                                                 logger.warning(f"Found mustSupport=true without path in element of {entry_key}")
                                            # --- End FIX ---
                                if ms_paths_for_sd: entry['ms_paths'] = ms_paths_for_sd # Store the set of paths
                                if has_ms: entry['ms_flag'] = True; logger.debug(f"  Found MS elements in {entry_key}") # Use boolean flag
                                entry['sd_processed'] = True # Mark MS check done

                        elif is_example: # JSON Example
                             key_to_use = None; profile_meta = data.get('meta', {}).get('profile', [])
                             if profile_meta and isinstance(profile_meta, list):
                                  for profile_url in profile_meta: profile_id_from_meta = profile_url.split('/')[-1];
                                  if profile_id_from_meta in resource_info: key_to_use = profile_id_from_meta; break
                             if not key_to_use: key_to_use = resource_type
                             if key_to_use not in resource_info: resource_info[key_to_use].update({'name': key_to_use, 'type': resource_type})
                             resource_info[key_to_use]['examples'].add(member.name)

                    elif is_example: # XML/HTML examples
                         # ... (XML/HTML example association logic) ...
                         guessed_type = base_filename_lower.split('-')[0].capitalize(); guessed_profile_id = base_filename_lower.split('-')[0]; key_to_use = None
                         if guessed_profile_id in resource_info: key_to_use = guessed_profile_id
                         elif guessed_type in resource_info: key_to_use = guessed_type
                         if key_to_use: resource_info[key_to_use]['examples'].add(member.name)
                         else: logger.warning(f"Could not associate non-JSON example {member.name}")

                except Exception as e: logger.warning(f"Could not process member {member.name}: {e}", exc_info=False)
                finally:
                     if fileobj: fileobj.close()
            # -- End Member Loop --

        # --- Final formatting moved INSIDE the main try block ---
        final_list = []; final_ms_elements = {}; final_examples = {}
        logger.debug(f"Formatting results from resource_info keys: {list(resource_info.keys())}")
        for key, info in resource_info.items():
            display_name = info.get('name') or key; base_type = info.get('type')
            if display_name or base_type:
                logger.debug(f"  Formatting item '{display_name}': type='{base_type}', profile='{info.get('is_profile', False)}', ms_flag='{info.get('ms_flag', False)}'")
                final_list.append({'name': display_name, 'type': base_type, 'is_profile': info.get('is_profile', False), 'must_support': info.get('ms_flag', False)}) # Ensure 'must_support' key uses 'ms_flag'
                if info['ms_paths']: final_ms_elements[display_name] = sorted(list(info['ms_paths']))
                if info['examples']: final_examples[display_name] = sorted(list(info['examples']))
            else: logger.warning(f"Skipping formatting for key: {key}")

        results['resource_types_info'] = sorted(final_list, key=lambda x: (not x.get('is_profile', False), x.get('name', '')))
        results['must_support_elements'] = final_ms_elements
        results['examples'] = final_examples
        # --- End formatting moved inside ---

    except Exception as e:
        err_msg = f"Error processing package file {tgz_path}: {e}"; logger.error(err_msg, exc_info=True); results['errors'].append(err_msg)

    # Logging counts
    final_types_count = len(results['resource_types_info']); ms_count = sum(1 for r in results['resource_types_info'] if r['must_support']); total_ms_paths = sum(len(v) for v in results['must_support_elements'].values()); total_examples = sum(len(v) for v in results['examples'].values())
    logger.info(f"V6.2 Extraction: {final_types_count} items ({ms_count} MS; {total_ms_paths} MS paths; {total_examples} examples) from {os.path.basename(tgz_path)}")

    return results