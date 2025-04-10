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
    try:
        instance_path = current_app.instance_path
    except RuntimeError:
        logger.warning("No app context for instance_path, constructing relative path.")
        instance_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'instance'))
        logger.debug(f"Constructed instance path: {instance_path}")
    download_dir = os.path.join(instance_path, DOWNLOAD_DIR_NAME)
    try:
        os.makedirs(download_dir, exist_ok=True)
        return download_dir
    except OSError as e:
        logger.error(f"Fatal Error: Could not create dir {download_dir}: {e}", exc_info=True)
        return None

def sanitize_filename_part(text):
    """Basic sanitization for creating filenames."""
    safe_text = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in text)
    safe_text = re.sub(r'_+', '_', safe_text)
    safe_text = safe_text.strip('_-.')
    return safe_text if safe_text else "invalid_name"

def _construct_tgz_filename(name, version):
    return f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.tgz"

# --- Helper to Find/Extract SD ---
def _find_and_extract_sd(tgz_path, resource_type_to_find):
    sd_data = None
    found_path = None
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            logger.debug(f"Searching for SD type '{resource_type_to_find}' in {tgz_path}")
            potential_paths = [
                 f'package/StructureDefinition-{resource_type_to_find.lower()}.json',
                 f'package/StructureDefinition-{resource_type_to_find}.json'
            ]
            member_found = None
            for potential_path in potential_paths:
                try:
                    member_found = tar.getmember(potential_path)
                    if member_found: break
                except KeyError:
                    pass

            if not member_found:
                for member in tar:
                    if member.isfile() and member.name.startswith('package/') and member.name.lower().endswith('.json'):
                        filename_lower = os.path.basename(member.name).lower()
                        if filename_lower in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                            continue
                        sd_fileobj = None
                        try:
                            sd_fileobj = tar.extractfile(member)
                            if sd_fileobj:
                                content_bytes = sd_fileobj.read(); content_string = content_bytes.decode('utf-8-sig'); data = json.loads(content_string)
                                if isinstance(data, dict) and data.get('resourceType') == 'StructureDefinition' and data.get('type') == resource_type_to_find:
                                    member_found = member
                                    break
                        except Exception:
                            pass
                        finally:
                            if sd_fileobj: sd_fileobj.close()

            if member_found:
                sd_fileobj = None
                try:
                    sd_fileobj = tar.extractfile(member_found)
                    if sd_fileobj:
                        content_bytes = sd_fileobj.read(); content_string = content_bytes.decode('utf-8-sig'); sd_data = json.loads(content_string)
                        found_path = member_found.name; logger.info(f"Found matching SD at path: {found_path}")
                except Exception as e:
                     logger.warning(f"Could not read/parse member {member_found.name} after finding it: {e}")
                     sd_data = None; found_path = None
                finally:
                     if sd_fileobj: sd_fileobj.close()

    except tarfile.TarError as e:
        logger.error(f"TarError reading {tgz_path}: {e}")
        raise
    except FileNotFoundError:
        logger.error(f"FileNotFoundError reading {tgz_path}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in _find_and_extract_sd for {tgz_path}: {e}", exc_info=True)
        raise
    return sd_data, found_path

# --- Core Service Functions ---

def download_package(name, version):
    logger = logging.getLogger(__name__)
    download_dir = _get_download_dir()
    if not download_dir: return None, "Could not get/create download directory."

    package_id = f"{name}#{version}"
    package_url = f"{FHIR_REGISTRY_BASE_URL}/{name}/{version}"
    filename = _construct_tgz_filename(name, version)
    save_path = os.path.join(download_dir, filename)

    if os.path.exists(save_path):
        logger.info(f"Package already exists: {filename}")
        return save_path, None

    logger.info(f"Downloading: {package_id} -> {filename}")
    try:
        with requests.get(package_url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        logger.info(f"Success: Downloaded {filename}")
        return save_path, None
    except requests.exceptions.RequestException as e: err_msg = f"Download error for {package_id}: {e}"; logger.error(err_msg); return None, err_msg
    except OSError as e: err_msg = f"File save error for {filename}: {e}"; logger.error(err_msg); return None, err_msg
    except Exception as e: err_msg = f"Unexpected download error for {package_id}: {e}"; logger.error(err_msg, exc_info=True); return None, err_msg

def extract_dependencies(tgz_path):
    logger = logging.getLogger(__name__)
    package_json_path = "package/package.json"
    dependencies = {}
    error_message = None
    if not tgz_path or not os.path.exists(tgz_path): return None, f"File not found at {tgz_path}"
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            package_json_member = tar.getmember(package_json_path)
            package_json_fileobj = tar.extractfile(package_json_member)
            if package_json_fileobj:
                try:
                    package_data = json.loads(package_json_fileobj.read().decode('utf-8-sig'))
                    dependencies = package_data.get('dependencies', {})
                finally: package_json_fileobj.close()
            else: raise FileNotFoundError(f"Could not extract {package_json_path}")
    except KeyError: error_message = f"'{package_json_path}' not found in {os.path.basename(tgz_path)}."; logger.warning(error_message)
    except (json.JSONDecodeError, UnicodeDecodeError) as e: error_message = f"Parse error in {package_json_path} from {os.path.basename(tgz_path)}: {e}"; logger.error(error_message); dependencies = None
    except (tarfile.TarError, FileNotFoundError) as e: error_message = f"Archive error {os.path.basename(tgz_path)}: {e}"; logger.error(error_message); dependencies = None
    except Exception as e: error_message = f"Unexpected error extracting deps: {e}"; logger.error(error_message, exc_info=True); dependencies = None
    return dependencies, error_message

def import_package_and_dependencies(initial_name, initial_version):
    logger = logging.getLogger(__name__)
    logger.info(f"Starting recursive import for {initial_name}#{initial_version}")
    results = {'requested': (initial_name, initial_version), 'processed': set(), 'downloaded': {}, 'all_dependencies': {}, 'errors': [] }
    pending_queue = [(initial_name, initial_version)]; processed_lookup = set()

    while pending_queue:
        name, version = pending_queue.pop(0)
        package_id_tuple = (name, version)

        if package_id_tuple in processed_lookup: continue

        logger.info(f"Processing: {name}#{version}"); processed_lookup.add(package_id_tuple)

        save_path, dl_error = download_package(name, version)

        if dl_error:
            error_msg = f"Download failed for {name}#{version}: {dl_error}"
            results['errors'].append(error_msg)
            if package_id_tuple == results['requested']:
                 logger.error("Aborting import: Initial package download failed.")
                 break
            else:
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
                          if dep_tuple not in processed_lookup:
                              if dep_tuple not in pending_queue:
                                   pending_queue.append(dep_tuple)
                                   logger.debug(f"Added to queue: {dep_name}#{dep_version}")
                     else:
                          logger.warning(f"Skipping invalid dependency entry '{dep_name}': '{dep_version}' in {name}#{version}")

    proc_count=len(results['processed']); dl_count=len(results['downloaded']); err_count=len(results['errors'])
    logger.info(f"Import finished. Processed: {proc_count}, Downloaded/Verified: {dl_count}, Errors: {err_count}")
    return results

def process_package_file(tgz_path):
    logger = logging.getLogger(__name__)
    logger.info(f"Processing package file details: {tgz_path}")
    results = {'resource_types_info': [], 'must_support_elements': {}, 'examples': {}, 'errors': [] }
    resource_info = {}

    if not os.path.exists(tgz_path):
        results['errors'].append(f"Package file not found: {tgz_path}")
        return results

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                member_name_lower = member.name.lower()
                base_filename_lower = os.path.basename(member_name_lower)
                fileobj = None

                if member.name.startswith('package/') and member_name_lower.endswith('.json') and \
                   base_filename_lower not in ['package.json', '.index.json', 'validation-summary.json']:
                    try:
                        fileobj = tar.extractfile(member)
                        if not fileobj:
                            continue
                        content_string = fileobj.read().decode('utf-8-sig')
                        data = json.loads(content_string)

                        if isinstance(data, dict) and data.get('resourceType'):
                            resource_type = data['resourceType']

                            if member.name.startswith('package/example/'):
                                ex_type = resource_type
                                entry = resource_info.setdefault(ex_type, {
                                    'base_type': ex_type,
                                    'ms_flag': False,
                                    'ms_paths': [],
                                    'examples': [],
                                    'sd_processed': False
                                })
                                entry['examples'].append(member.name)
                                continue

                            if resource_type == 'StructureDefinition':
                                profile_id = data.get('id') or data.get('name')
                                fhir_type = data.get('type')

                                if not profile_id:
                                    logger.warning(f"StructureDefinition missing id or name: {member.name}")
                                    continue

                                entry = resource_info.setdefault(profile_id, {
                                    'base_type': fhir_type,
                                    'ms_flag': False,
                                    'ms_paths': [],
                                    'examples': [],
                                    'sd_processed': False
                                })

                                if entry['sd_processed']:
                                    continue

                                ms_paths = []
                                has_ms = False
                                for element_list in [data.get('snapshot', {}).get('element', []), data.get('differential', {}).get('element', [])]:
                                    for element in element_list:
                                        if not isinstance(element, dict):
                                            continue
                                        if element.get('mustSupport') is True:
                                            path = element.get('path')
                                            if path:
                                                ms_paths.append(path)
                                                has_ms = True
                                            for t in element.get('type', []):
                                                for ext in t.get('extension', []):
                                                    ext_url = ext.get('url')
                                                    if ext_url:
                                                        ms_paths.append(f"{path}.type.extension[{ext_url}]")
                                                        has_ms = True
                                            for ext in element.get('extension', []):
                                                ext_url = ext.get('url')
                                                if ext_url:
                                                    ms_paths.append(f"{path}.extension[{ext_url}]")
                                                    has_ms = True
                                if ms_paths:
                                    entry['ms_paths'] = sorted(set(ms_paths))
                                if has_ms:
                                    entry['ms_flag'] = True
                                entry['sd_processed'] = True

                    except Exception as e:
                        logger.warning(f"Could not read/parse member {member.name}: {e}")
                    finally:
                        if fileobj:
                            fileobj.close()

                elif (member.name.startswith('package/example/') or ('example' in base_filename_lower and member.name.startswith('package/'))) \
                     and (member_name_lower.endswith('.xml') or member_name_lower.endswith('.html')):
                    guessed_type = base_filename_lower.split('-', 1)[0].capitalize()
                    if guessed_type in resource_info:
                        resource_info[guessed_type]['examples'].append(member.name)

    except Exception as e:
        err_msg = f"Error processing package file {tgz_path}: {e}"
        logger.error(err_msg, exc_info=True)
        results['errors'].append(err_msg)

    # --- New logic: merge profiles of same base_type ---
    merged_info = {}
    grouped_by_type = defaultdict(list)

    for profile_id, entry in resource_info.items():
        base_type = entry['base_type'] or profile_id
        grouped_by_type[base_type].append((profile_id, entry))

    for base_type, profiles in grouped_by_type.items():
        merged_paths = set()
        merged_examples = []
        has_ms = False

        for _, profile_entry in profiles:
            merged_paths.update(profile_entry.get('ms_paths', []))
            merged_examples.extend(profile_entry.get('examples', []))
            if profile_entry.get('ms_flag'):
                has_ms = True

        merged_info[base_type] = {
            'base_type': base_type,
            'ms_flag': has_ms,
            'ms_paths': sorted(merged_paths),
            'examples': sorted(merged_examples),
        }

    results['resource_types_info'] = sorted([
        {'name': k, 'base_type': v.get('base_type'), 'must_support': v['ms_flag']}
        for k, v in merged_info.items()
    ], key=lambda x: x['name'])

    results['must_support_elements'] = {
        k: v['ms_paths'] for k, v in merged_info.items() if v['ms_paths']
    }

    results['examples'] = {
        k: v['examples'] for k, v in merged_info.items() if v['examples']
    }

    logger.info(f"Extracted {len(results['resource_types_info'])} profiles "
                f"({sum(1 for r in results['resource_types_info'] if r['must_support'])} with MS; "
                f"{sum(len(v) for v in results['must_support_elements'].values())} MS paths; "
                f"{sum(len(v) for v in results['examples'].values())} examples) from {tgz_path}")

    return results

# --- Remove or Comment Out old/unused functions ---
# def _fetch_package_metadata(package_name, package_version): ... (REMOVED)
# def resolve_all_dependencies(initial_package_name, initial_package_version): ... (REMOVED)
# def process_ig_import(package_name, package_version): ... (OLD orchestrator - REMOVED)