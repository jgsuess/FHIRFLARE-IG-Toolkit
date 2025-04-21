import requests
import os
import tarfile
import json
import re
import logging
import shutil
import sqlite3
from flask import current_app, Blueprint, request, jsonify
from fhirpathpy import evaluate
from collections import defaultdict
from pathlib import Path
import datetime
import subprocess
import tempfile
import xml.etree.ElementTree as ET

# Define Blueprint
services_bp = Blueprint('services', __name__)

# Configure logging
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
else:
    pass
logger = logging.getLogger(__name__)

# --- Constants ---
FHIR_REGISTRY_BASE_URL = "https://packages.fhir.org"
DOWNLOAD_DIR_NAME = "fhir_packages"
CANONICAL_PACKAGE = ("hl7.fhir.r4.core", "4.0.1")
CANONICAL_PACKAGE_ID = f"{CANONICAL_PACKAGE[0]}#{CANONICAL_PACKAGE[1]}"

# Define standard FHIR R4 base types
FHIR_R4_BASE_TYPES = {
    "Account", "ActivityDefinition", "AdministrableProductDefinition", "AdverseEvent", "AllergyIntolerance",
    "Appointment", "AppointmentResponse", "AuditEvent", "Basic", "Binary", "BiologicallyDerivedProduct",
    "BodyStructure", "Bundle", "CapabilityStatement", "CarePlan", "CareTeam", "CatalogEntry", "ChargeItem",
    "ChargeItemDefinition", "Claim", "ClaimResponse", "ClinicalImpression", "CodeSystem", "Communication",
    "CommunicationRequest", "CompartmentDefinition", "Composition", "ConceptMap", "Condition", "Consent",
    "Contract", "Coverage", "CoverageEligibilityRequest", "CoverageEligibilityResponse", "DetectedIssue",
    "Device", "DeviceDefinition", "DeviceMetric", "DeviceRequest", "DeviceUseStatement", "DiagnosticReport",
    "DocumentManifest", "DocumentReference", "DomainResource", "EffectEvidenceSynthesis", "Encounter",
    "Endpoint", "EnrollmentRequest", "EnrollmentResponse", "EpisodeOfCare", "EventDefinition", "Evidence",
    "EvidenceVariable", "ExampleScenario", "ExplanationOfBenefit", "FamilyMemberHistory", "Flag", "Goal",
    "GraphDefinition", "Group", "GuidanceResponse", "HealthcareService", "ImagingStudy", "Immunization",
    "ImmunizationEvaluation", "ImmunizationRecommendation", "ImplementationGuide", "InsurancePlan",
    "Invoice", "Library", "Linkage", "List", "Location", "Measure", "MeasureReport", "Media", "Medication",
    "MedicationAdministration", "MedicationDispense", "MedicationKnowledge", "MedicationRequest",
    "MedicationStatement", "MedicinalProduct", "MedicinalProductAuthorization", "MedicinalProductContraindication",
    "MedicinalProductIndication", "MedicinalProductIngredient", "MedicinalProductInteraction",
    "MedicinalProductManufactured", "MedicinalProductPackaged", "MedicinalProductPharmaceutical",
    "MedicinalProductUndesirableEffect", "MessageDefinition", "MessageHeader", "MolecularSequence",
    "NamingSystem", "NutritionOrder", "Observation", "ObservationDefinition", "OperationDefinition",
    "OperationOutcome", "Organization", "OrganizationAffiliation", "Patient", "PaymentNotice",
    "PaymentReconciliation", "Person", "PlanDefinition", "Practitioner", "PractitionerRole", "Procedure",
    "Provenance", "Questionnaire", "QuestionnaireResponse", "RelatedPerson", "RequestGroup", "ResearchDefinition",
    "ResearchElementDefinition", "ResearchStudy", "ResearchSubject", "Resource", "RiskAssessment",
    "RiskEvidenceSynthesis", "Schedule", "SearchParameter", "ServiceRequest", "Slot", "Specimen",
    "SpecimenDefinition", "StructureDefinition", "StructureMap", "Subscription", "Substance",
    "SubstanceNucleicAcid", "SubstancePolymer", "SubstanceProtein", "SubstanceReferenceInformation",
    "SubstanceSourceMaterial", "SubstanceSpecification", "SupplyDelivery", "SupplyRequest", "Task",
    "TerminologyCapabilities", "TestReport", "TestScript", "ValueSet", "VerificationResult", "VisionPrescription"
}

# --- Helper Functions ---

def _get_download_dir():
    """Gets the absolute path to the configured FHIR package download directory."""
    packages_dir = None
    try:
        packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
        if packages_dir:
            logger.debug(f"Using FHIR_PACKAGES_DIR from current_app config: {packages_dir}")
        else:
            logger.warning("FHIR_PACKAGES_DIR not found in current_app config.")
            instance_path = current_app.instance_path
            packages_dir = os.path.join(instance_path, DOWNLOAD_DIR_NAME)
            logger.warning(f"Falling back to default packages path: {packages_dir}")
    except RuntimeError:
        logger.warning("No app context found. Constructing default relative path for packages.")
        script_dir = os.path.dirname(__file__)
        instance_path_fallback = os.path.abspath(os.path.join(script_dir, '..', 'instance'))
        packages_dir = os.path.join(instance_path_fallback, DOWNLOAD_DIR_NAME)
        logger.debug(f"Constructed fallback packages path: {packages_dir}")
    if not packages_dir:
        logger.error("Fatal Error: Could not determine FHIR packages directory.")
        return None
    try:
        os.makedirs(packages_dir, exist_ok=True)
        return packages_dir
    except OSError as e:
        logger.error(f"Fatal Error creating packages directory {packages_dir}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting/creating packages directory {packages_dir}: {e}", exc_info=True)
        return None

def sanitize_filename_part(text):
    """Basic sanitization for name/version parts of filename."""
    if not isinstance(text, str):
        text = str(text)
    safe_text = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in text)
    safe_text = re.sub(r'_+', '_', safe_text)
    safe_text = safe_text.strip('_-.')
    return safe_text if safe_text else "invalid_name"

def construct_tgz_filename(name, version):
    """Constructs the standard FHIR package filename using sanitized parts."""
    if not name or not version:
        logger.error(f"Cannot construct filename with missing name ('{name}') or version ('{version}')")
        return None
    return f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.tgz"

def construct_metadata_filename(name, version):
    """Constructs the standard metadata filename."""
    if not name or not version:
        logger.error(f"Cannot construct metadata filename with missing name ('{name}') or version ('{version}')")
        return None
    return f"{sanitize_filename_part(name)}-{sanitize_filename_part(version)}.metadata.json"

def parse_package_filename(filename):
    """Parses a standard FHIR package filename into name and version."""
    if not filename or not filename.endswith('.tgz'):
        logger.debug(f"Filename '{filename}' does not end with .tgz")
        return None, None
    base_name = filename[:-4]
    last_hyphen_index = base_name.rfind('-')
    while last_hyphen_index != -1:
        potential_name = base_name[:last_hyphen_index]
        potential_version = base_name[last_hyphen_index + 1:]
        if potential_version and (potential_version[0].isdigit() or any(potential_version.startswith(kw) for kw in ['v', 'dev', 'draft', 'preview', 'release', 'alpha', 'beta'])):
            name = potential_name.replace('_', '.')
            version = potential_version
            logger.debug(f"Parsed '{filename}' -> name='{name}', version='{version}'")
            return name, version
        else:
            last_hyphen_index = base_name.rfind('-', 0, last_hyphen_index)
    logger.warning(f"Could not parse version from '{filename}'. Treating '{base_name}' as name.")
    name = base_name.replace('_', '.')
    version = ""
    return name, version

def remove_narrative(resource):
    """Remove narrative text element from a FHIR resource."""
    if isinstance(resource, dict):
        if 'text' in resource:
            logger.debug(f"Removing narrative text from resource: {resource.get('resourceType', 'unknown')}")
            del resource['text']
        if resource.get('resourceType') == 'Bundle' and 'entry' in resource:
            resource['entry'] = [dict(entry, resource=remove_narrative(entry.get('resource'))) if entry.get('resource') else entry for entry in resource['entry']]
    return resource

def get_cached_structure(package_name, package_version, resource_type, view):
    """Retrieve cached StructureDefinition from SQLite."""
    try:
        conn = sqlite3.connect(os.path.join(current_app.instance_path, 'fhir_ig.db'))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT structure_data FROM structure_cache
            WHERE package_name = ? AND package_version = ? AND resource_type = ? AND view = ?
        """, (package_name, package_version, resource_type, view))
        result = cursor.fetchone()
        conn.close()
        if result:
            logger.debug(f"Cache hit for {package_name}#{package_version}:{resource_type}:{view}")
            return json.loads(result[0])
        logger.debug(f"No cache entry for {package_name}#{package_version}:{resource_type}:{view}")
        return None
    except Exception as e:
        logger.error(f"Error accessing structure cache: {e}", exc_info=True)
        return None

def cache_structure(package_name, package_version, resource_type, view, structure_data):
    """Cache StructureDefinition in SQLite."""
    try:
        conn = sqlite3.connect(os.path.join(current_app.instance_path, 'fhir_ig.db'))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS structure_cache (
                package_name TEXT,
                package_version TEXT,
                resource_type TEXT,
                view TEXT,
                structure_data TEXT,
                PRIMARY KEY (package_name, package_version, resource_type, view)
            )
        """)
        cursor.execute("""
            INSERT OR REPLACE INTO structure_cache
            (package_name, package_version, resource_type, view, structure_data)
            VALUES (?, ?, ?, ?, ?)
        """, (package_name, package_version, resource_type, view, json.dumps(structure_data)))
        conn.commit()
        conn.close()
        logger.debug(f"Cached structure for {package_name}#{package_version}:{resource_type}:{view}")
    except Exception as e:
        logger.error(f"Error caching structure: {e}", exc_info=True)

def find_and_extract_sd(tgz_path, resource_identifier, profile_url=None):
    """Helper to find and extract StructureDefinition json from a tgz path, prioritizing profile match."""
    sd_data = None
    found_path = None
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"File not found in find_and_extract_sd: {tgz_path}")
        return None, None
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            logger.debug(f"Searching for SD matching '{resource_identifier}' with profile '{profile_url}' in {os.path.basename(tgz_path)}")
            # Store potential matches to evaluate the best one at the end
            potential_matches = [] # Store tuples of (precision_score, data, member_name)

            for member in tar:
                if not (member.isfile() and member.name.startswith('package/') and member.name.lower().endswith('.json')):
                    continue
                # Skip common metadata files
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
                            sd_type = data.get('type')
                            sd_url = data.get('url')
                            sd_filename_base = os.path.splitext(os.path.basename(member.name))[0]
                            sd_filename_lower = sd_filename_base.lower()
                            resource_identifier_lower = resource_identifier.lower() if resource_identifier else None

                            # logger.debug(f"Checking SD: id={sd_id}, name={sd_name}, type={sd_type}, url={sd_url}, file={sd_filename_lower} against identifier='{resource_identifier}'")

                            match_score = 0 # Higher score means more precise match

                            # Highest precision: Exact match on profile_url
                            if profile_url and sd_url == profile_url:
                                match_score = 5
                                logger.debug(f"Exact match found based on profile_url: {profile_url}")
                                # If we find the exact profile URL, this is the best possible match.
                                sd_data = remove_narrative(data)
                                found_path = member.name
                                logger.info(f"Found definitive SD matching profile '{profile_url}' at path: {found_path}. Stopping search.")
                                break # Stop searching immediately

                            # Next highest precision: Exact match on id or name
                            elif resource_identifier_lower:
                                if sd_id and resource_identifier_lower == sd_id.lower():
                                    match_score = 4
                                    logger.debug(f"Match found based on exact sd_id: {sd_id}")
                                elif sd_name and resource_identifier_lower == sd_name.lower():
                                     match_score = 4
                                     logger.debug(f"Match found based on exact sd_name: {sd_name}")
                                # Next: Match filename pattern "StructureDefinition-{identifier}.json"
                                elif sd_filename_lower == f"structuredefinition-{resource_identifier_lower}":
                                     match_score = 3
                                     logger.debug(f"Match found based on exact filename pattern: {member.name}")
                                 # Next: Match on type ONLY if the identifier looks like a base type (no hyphens/dots)
                                elif sd_type and resource_identifier_lower == sd_type.lower() and not re.search(r'[-.]', resource_identifier):
                                      match_score = 2
                                      logger.debug(f"Match found based on sd_type (simple identifier): {sd_type}")
                                 # Lower precision: Check if identifier is IN the filename
                                elif resource_identifier_lower in sd_filename_lower:
                                      match_score = 1
                                      logger.debug(f"Potential match based on identifier in filename: {member.name}")
                                 # Lowest precision: Check if identifier is IN the URL
                                elif sd_url and resource_identifier_lower in sd_url.lower():
                                      match_score = 1
                                      logger.debug(f"Potential match based on identifier in url: {sd_url}")

                            if match_score > 0:
                                potential_matches.append((match_score, remove_narrative(data), member.name))

                                # If it's a very high precision match, we can potentially break early
                                if match_score >= 3: # Exact ID, Name, or Filename pattern
                                     logger.info(f"Found high-confidence match for '{resource_identifier}' ({member.name}), stopping search.")
                                     # Set sd_data here and break
                                     sd_data = remove_narrative(data)
                                     found_path = member.name
                                     break

                except json.JSONDecodeError as e:
                    logger.debug(f"Could not parse JSON in {member.name}, skipping: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode UTF-8 in {member.name}, skipping: {e}")
                except tarfile.TarError as e:
                    logger.warning(f"Tar error reading member {member.name}, skipping: {e}")
                except Exception as e:
                    logger.warning(f"Could not read/parse potential SD {member.name}, skipping: {e}", exc_info=False)
                finally:
                    if fileobj:
                        fileobj.close()

            # If the loop finished without finding an exact profile_url or high-confidence match (score >= 3)
            if not sd_data and potential_matches:
                # Sort potential matches by score (highest first)
                potential_matches.sort(key=lambda x: x[0], reverse=True)
                best_match = potential_matches[0]
                sd_data = best_match[1]
                found_path = best_match[2]
                logger.info(f"Selected best match for '{resource_identifier}' from potential matches (Score: {best_match[0]}): {found_path}")

            if sd_data is None:
                logger.info(f"SD matching identifier '{resource_identifier}' or profile '{profile_url}' not found within archive {os.path.basename(tgz_path)}")

    except tarfile.ReadError as e:
        logger.error(f"Tar ReadError reading {tgz_path}: {e}")
        return None, None
    except tarfile.TarError as e:
        logger.error(f"TarError reading {tgz_path} in find_and_extract_sd: {e}")
        raise # Re-raise critical tar errors
    except FileNotFoundError:
        logger.error(f"FileNotFoundError reading {tgz_path} in find_and_extract_sd.")
        raise # Re-raise critical file errors
    except Exception as e:
        logger.error(f"Unexpected error in find_and_extract_sd for {tgz_path}: {e}", exc_info=True)
        raise # Re-raise unexpected errors

    return sd_data, found_path

# --- Metadata Saving/Loading ---
def save_package_metadata(name, version, dependency_mode, dependencies, complies_with_profiles=None, imposed_profiles=None):
    """Saves dependency mode, imported dependencies, and profile relationships as metadata."""
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Could not get download directory for metadata saving.")
        return False
    metadata_filename = construct_metadata_filename(name, version)
    if not metadata_filename: return False
    metadata_path = os.path.join(download_dir, metadata_filename)
    metadata = {
        'package_name': name,
        'version': version,
        'dependency_mode': dependency_mode,
        'imported_dependencies': dependencies or [],
        'complies_with_profiles': complies_with_profiles or [],
        'imposed_profiles': imposed_profiles or [],
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    try:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        logger.info(f"Saved metadata for {name}#{version} at {metadata_path}")
        return True
    except IOError as e:
        logger.error(f"Failed to write metadata file {metadata_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error saving metadata for {name}#{version}: {e}", exc_info=True)
        return False

def get_package_metadata(name, version):
    """Retrieves the metadata for a given package."""
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Could not get download directory for metadata retrieval.")
        return None
    metadata_filename = construct_metadata_filename(name, version)
    if not metadata_filename: return None
    metadata_path = os.path.join(download_dir, metadata_filename)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to read or parse metadata file {metadata_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error reading metadata for {name}#{version}: {e}", exc_info=True)
            return None
    else:
        logger.debug(f"Metadata file not found: {metadata_path}")
        return None

def process_package_file(tgz_path):
    """Extracts types, profile status, MS elements, examples, and profile relationships from a downloaded .tgz package."""
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"Package file not found for processing: {tgz_path}")
        return {'errors': [f"Package file not found: {tgz_path}"], 'resource_types_info': []}

    pkg_basename = os.path.basename(tgz_path)
    name, version = parse_package_filename(pkg_basename)
    logger.info(f"Processing package file details: {pkg_basename}")

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
        'examples': set(),
        'sd_processed': False,
        'optional_usage': False
    })
    referenced_types = set()

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            members = tar.getmembers()
            logger.debug(f"Found {len(members)} members in {pkg_basename}")

            # Pass 1: Process StructureDefinitions
            logger.debug("Processing StructureDefinitions...")
            sd_members = [m for m in members if m.isfile() and m.name.startswith('package/') and m.name.lower().endswith('.json')]

            for member in sd_members:
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
                        continue

                    # Remove narrative text element
                    data = remove_narrative(data)

                    profile_id = data.get('id') or data.get('name')
                    sd_type = data.get('type')
                    sd_base = data.get('baseDefinition')
                    is_profile_sd = bool(sd_base)

                    if not profile_id or not sd_type:
                        logger.warning(f"Skipping StructureDefinition in {member.name} due to missing ID ('{profile_id}') or Type ('{sd_type}').")
                        continue

                    entry_key = profile_id
                    entry = resource_info[entry_key]

                    if entry.get('sd_processed'): continue

                    logger.debug(f"Processing StructureDefinition: {entry_key} (type={sd_type}, is_profile={is_profile_sd})")

                    entry['name'] = entry_key
                    entry['type'] = sd_type
                    entry['is_profile'] = is_profile_sd
                    entry['sd_processed'] = True
                    referenced_types.add(sd_type)

                    # Cache StructureDefinition for all views
                    views = ['differential', 'snapshot', 'must-support', 'key-elements']
                    for view in views:
                        view_elements = data.get('differential', {}).get('element', []) if view == 'differential' else data.get('snapshot', {}).get('element', [])
                        if view == 'must-support':
                            view_elements = [e for e in view_elements if e.get('mustSupport')]
                        elif view == 'key-elements':
                            key_elements = set()
                            differential_elements = data.get('differential', {}).get('element', [])
                            for e in view_elements:
                                is_in_differential = any(de['id'] == e['id'] for de in differential_elements)
                                if e.get('mustSupport') or is_in_differential or e.get('min', 0) > 0 or e.get('max') != '*' or e.get('slicing') or e.get('constraint'):
                                    key_elements.add(e['id'])
                                    parent_path = e['path']
                                    while '.' in parent_path:
                                        parent_path = parent_path.rsplit('.', 1)[0]
                                        parent_element = next((el for el in view_elements if el['path'] == parent_path), None)
                                        if parent_element:
                                            key_elements.add(parent_element['id'])
                            view_elements = [e for e in view_elements if e['id'] in key_elements]
                        cache_data = {
                            'structure_definition': data,
                            'must_support_paths': [],
                            'fallback_used': False,
                            'source_package': f"{name}#{version}"
                        }
                        cache_structure(name, version, sd_type, view, cache_data)

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
                    results['complies_with_profiles'].extend(c for c in complies_with if c not in results['complies_with_profiles'])
                    results['imposed_profiles'].extend(i for i in imposed if i not in results['imposed_profiles'])

                    # Must Support and Optional Usage Logic
                    has_ms_in_this_sd = False
                    ms_paths_in_this_sd = set()
                    elements = data.get('snapshot', {}).get('element', []) or data.get('differential', {}).get('element', [])

                    for element in elements:
                        if not isinstance(element, dict):
                            continue
                        must_support = element.get('mustSupport')
                        element_id = element.get('id')
                        element_path = element.get('path')
                        slice_name = element.get('sliceName')

                        logger.debug(f"Checking element in {entry_key}: id={element_id}, path={element_path}, sliceName={slice_name}, mustSupport={must_support}")

                        if must_support is True:
                            if element_id and element_path:
                                ms_path = f"{element_path}[sliceName='{slice_name}']" if slice_name else element_id
                                ms_paths_in_this_sd.add(ms_path)
                                has_ms_in_this_sd = True
                                logger.info(f"Found MS element in {entry_key}: path={element_path}, id={element_id}, sliceName={slice_name}, ms_path={ms_path}")
                            else:
                                logger.warning(f"Found mustSupport=true without path/id in element of {entry_key} ({member.name})")
                                has_ms_in_this_sd = True

                    if has_ms_in_this_sd:
                        entry['ms_paths'].update(ms_paths_in_this_sd)
                        entry['ms_flag'] = True
                        logger.debug(f"Profile {entry_key} has MS flag set. MS paths: {entry['ms_paths']}")

                    if sd_type == 'Extension' and has_ms_in_this_sd:
                        internal_ms_exists = any(p.startswith('Extension.') or ':' in p for p in entry['ms_paths'])
                        if internal_ms_exists:
                            entry['optional_usage'] = True
                            logger.info(f"Marked Extension {entry_key} as optional_usage: MS paths={entry['ms_paths']}")
                        else:
                            logger.debug(f"Extension {entry_key} has MS flag but no internal MS elements: {entry['ms_paths']}")

                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse JSON SD in {member.name}: {e}")
                    results['errors'].append(f"JSON parse error in {member.name}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode SD in {member.name}: {e}")
                    results['errors'].append(f"Encoding error in {member.name}")
                except Exception as e:
                    logger.warning(f"Could not process SD member {member.name}: {e}", exc_info=False)
                    results['errors'].append(f"Processing error in {member.name}: {e}")
                finally:
                    if fileobj: fileobj.close()

            # Pass 2: Process Examples
            logger.debug("Processing Examples...")
            example_members = [m for m in members if m.isfile() and m.name.startswith('package/') and 'example' in m.name.lower()]

            for member in example_members:
                base_filename_lower = os.path.basename(member.name).lower()
                if base_filename_lower in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue

                logger.debug(f"Processing potential example file: {member.name}")
                is_json = member.name.lower().endswith('.json')
                fileobj = None
                associated_key = None

                try:
                    fileobj = tar.extractfile(member)
                    if not fileobj: continue

                    if is_json:
                        content_bytes = fileobj.read()
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)

                        if not isinstance(data, dict): continue
                        resource_type = data.get('resourceType')
                        if not resource_type: continue

                        # Remove narrative text element
                        data = remove_narrative(data)

                        profile_meta = data.get('meta', {}).get('profile', [])
                        found_profile_match = False
                        if profile_meta and isinstance(profile_meta, list):
                            for profile_url in profile_meta:
                                if profile_url and isinstance(profile_url, str):
                                    profile_id_from_meta = profile_url.split('/')[-1]
                                    if profile_id_from_meta in resource_info:
                                        associated_key = profile_id_from_meta
                                        found_profile_match = True
                                        logger.debug(f"Example {member.name} associated with profile {associated_key} via meta.profile")
                                        break
                                    elif profile_url in resource_info:
                                        associated_key = profile_url
                                        found_profile_match = True
                                        logger.debug(f"Example {member.name} associated with profile {associated_key} via meta.profile")
                                        break

                        if not found_profile_match:
                            key_to_use = resource_type
                            if key_to_use not in resource_info:
                                resource_info[key_to_use].update({'name': key_to_use, 'type': resource_type, 'is_profile': False})
                            associated_key = key_to_use
                            logger.debug(f"Example {member.name} associated with resource type {associated_key}")
                        referenced_types.add(resource_type)
                    else:
                        guessed_type = base_filename_lower.split('-')[0].capitalize()
                        guessed_profile_id = base_filename_lower.split('-')[0]
                        key_to_use = None
                        if guessed_profile_id in resource_info:
                            key_to_use = guessed_profile_id
                        elif guessed_type in resource_info:
                            key_to_use = guessed_type
                        else:
                            key_to_use = guessed_type
                            resource_info[key_to_use].update({'name': key_to_use, 'type': key_to_use, 'is_profile': False})
                        associated_key = key_to_use
                        logger.debug(f"Non-JSON Example {member.name} associated with profile/type {associated_key}")
                        referenced_types.add(guessed_type)

                    if associated_key:
                        resource_info[associated_key]['examples'].add(member.name)
                    else:
                        logger.warning(f"Could not associate example {member.name} with any known resource or profile.")

                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse JSON example in {member.name}: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode example in {member.name}: {e}")
                except tarfile.TarError as e:
                    logger.warning(f"TarError reading example {member.name}: {e}")
                except Exception as e:
                    logger.warning(f"Could not process example member {member.name}: {e}", exc_info=False)
                finally:
                    if fileobj: fileobj.close()

            # Pass 3: Ensure Relevant Base Types
            logger.debug("Ensuring relevant FHIR R4 base types...")
            essential_types = {'CapabilityStatement'}
            for type_name in referenced_types | essential_types:
                if type_name in FHIR_R4_BASE_TYPES and type_name not in resource_info:
                    resource_info[type_name]['name'] = type_name
                    resource_info[type_name]['type'] = type_name
                    resource_info[type_name]['is_profile'] = False
                    logger.debug(f"Added base type entry for {type_name}")

            # Final Consolidation
            final_list = []
            final_ms_elements = {}
            final_examples = {}
            logger.debug(f"Finalizing results from resource_info keys: {list(resource_info.keys())}")

            for key, info in resource_info.items():
                display_name = info.get('name') or key
                base_type = info.get('type')

                if not display_name or not base_type:
                    logger.warning(f"Skipping final formatting for incomplete key: {key} - Info: {info}")
                    continue

                logger.debug(f"Finalizing item '{display_name}': type='{base_type}', is_profile='{info.get('is_profile', False)}', ms_flag='{info.get('ms_flag', False)}', optional_usage='{info.get('optional_usage', False)}'")
                final_list.append({
                    'name': display_name,
                    'type': base_type,
                    'is_profile': info.get('is_profile', False),
                    'must_support': info.get('ms_flag', False),
                    'optional_usage': info.get('optional_usage', False)
                })
                if info['ms_paths']:
                    final_paths = []
                    for path in info['ms_paths']:
                        if ':' in path and info['type'] == 'Extension':
                            final_paths.append(path)
                        else:
                            final_paths.append(path)
                    final_ms_elements[display_name] = sorted(final_paths)
                if info['examples']:
                    final_examples[display_name] = sorted(list(info['examples']))

            results['resource_types_info'] = sorted(final_list, key=lambda x: (not x.get('is_profile', False), x.get('name', '')))
            results['must_support_elements'] = final_ms_elements
            results['examples'] = final_examples

            logger.debug(f"Final must_support_elements: {json.dumps(final_ms_elements, indent=2)}")
            logger.debug(f"Final examples: {json.dumps(final_examples, indent=2)}")

    except tarfile.ReadError as e:
        err_msg = f"Tar ReadError processing package file {pkg_basename}: {e}"
        logger.error(err_msg)
        results['errors'].append(err_msg)
    except tarfile.TarError as e:
        err_msg = f"TarError processing package file {pkg_basename}: {e}"
        logger.error(err_msg)
        results['errors'].append(err_msg)
    except FileNotFoundError:
        err_msg = f"Package file not found during processing: {tgz_path}"
        logger.error(err_msg)
        results['errors'].append(err_msg)
    except Exception as e:
        err_msg = f"Unexpected error processing package file {pkg_basename}: {e}"
        logger.error(err_msg, exc_info=True)
        results['errors'].append(err_msg)

    final_types_count = len(results['resource_types_info'])
    ms_count = sum(1 for r in results['resource_types_info'] if r.get('must_support'))
    optional_ms_count = sum(1 for r in results['resource_types_info'] if r.get('optional_usage'))
    total_ms_paths = sum(len(v) for v in results['must_support_elements'].values())
    total_examples = sum(len(v) for v in results['examples'].values())
    logger.info(f"Package processing finished for {pkg_basename}: "
                f"{final_types_count} Resources/Profiles identified; "
                f"{ms_count} have MS elements ({optional_ms_count} are Optional Extensions w/ MS); "
                f"{total_ms_paths} total unique MS paths found across all profiles; "
                f"{total_examples} examples associated. "
                f"CompliesWith: {len(results['complies_with_profiles'])}, Imposed: {len(results['imposed_profiles'])}. "
                f"Errors during processing: {len(results['errors'])}")

    return results

# --- Validation Functions ---

def _legacy_navigate_fhir_path(resource, path, extension_url=None):
    """Navigates a FHIR resource using a FHIRPath-like expression, handling nested structures."""
    logger.debug(f"Navigating FHIR path: {path}")
    if not resource or not path:
        return None
    parts = path.split('.')
    current = resource
    resource_type = resource.get('resourceType')
    for i, part in enumerate(parts):
        # Skip resource type prefix (e.g., Patient)
        if i == 0 and part == resource_type:
            continue
        # Handle array indexing (e.g., name[0])
        match = re.match(r'^(\w+)\[(\d+)\]$', part)
        if match:
            key, index = match.groups()
            index = int(index)
            if isinstance(current, dict) and key in current:
                if isinstance(current[key], list) and index < len(current[key]):
                    current = current[key][index]
                else:
                    logger.debug(f"Path {part} invalid: key={key}, index={index}, current={current.get(key)}")
                    return None
            elif isinstance(current, list) and index < len(current):
                current = current[index]
            else:
                logger.debug(f"Path {part} not found in current={current}")
                return None
        else:
            # Handle choice types (e.g., onset[x])
            if '[x]' in part:
                part = part.replace('[x]', '')
                # Try common choice type suffixes
                for suffix in ['', 'DateTime', 'Age', 'Period', 'Range', 'String', 'CodeableConcept']:
                    test_key = part + suffix
                    if isinstance(current, dict) and test_key in current:
                        current = current[test_key]
                        break
                else:
                    logger.debug(f"Choice type {part}[x] not found in current={current}")
                    return None
            elif isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    # Handle FHIR complex types
                    if part == 'code' and 'coding' in current and isinstance(current['coding'], list) and current['coding']:
                        current = current['coding']
                    elif part == 'patient' and 'reference' in current and current['reference']:
                        current = current['reference']
                    elif part == 'manifestation' and isinstance(current, list) and current and 'coding' in current[0] and current[0]['coding']:
                        current = current[0]['coding']
                    elif part == 'clinicalStatus' and 'coding' in current and isinstance(current['coding'], list) and current['coding']:
                        current = current['coding']
                    else:
                        logger.debug(f"Path {part} not found in current={current}")
                        return None
            elif isinstance(current, list) and len(current) > 0:
                # Try to find the part in list items
                found = False
                for item in current:
                    if isinstance(item, dict) and part in item:
                        current = item[part]
                        found = True
                        break
                if not found:
                    # For nested paths like communication.language, return None only if the parent is absent
                    logger.debug(f"Path {part} not found in list items: {current}")
                    return None
    if extension_url and isinstance(current, list):
        current = [item for item in current if item.get('url') == extension_url]
    # Return non-None/non-empty values as present
    result = current if (current is not None and (not isinstance(current, list) or current)) else None
    logger.debug(f"Path {path} resolved to: {result}")
    return result

def navigate_fhir_path(resource, path, extension_url=None):
    """Navigates a FHIR resource using FHIRPath expressions."""
    logger.debug(f"Navigating FHIR path: {path}, extension_url={extension_url}")
    if not resource or not path:
        return None
    try:
        # Adjust path for extension filtering
        if extension_url and 'extension' in path:
            path = f"{path}[url='{extension_url}']"
        result = evaluate(resource, path)
        # Return first result if list, None if empty
        return result[0] if result else None
    except Exception as e:
        logger.error(f"FHIRPath evaluation failed for {path}: {e}")
        # Fallback to legacy navigation for compatibility
        return _legacy_navigate_fhir_path(resource, path, extension_url)

def _legacy_validate_resource_against_profile(package_name, version, resource, include_dependencies=True):
    """Validates a FHIR resource against a StructureDefinition in the specified package."""
    logger.debug(f"Validating resource {resource.get('resourceType')} against {package_name}#{version}, include_dependencies={include_dependencies}")
    result = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'details': [],  # Enhanced info for future use
        'resource_type': resource.get('resourceType'),
        'resource_id': resource.get('id', 'unknown'),
        'profile': resource.get('meta', {}).get('profile', [None])[0]
    }
    download_dir = _get_download_dir()
    if not download_dir:
        result['valid'] = False
        result['errors'].append("Could not access download directory")
        result['details'].append({
            'issue': "Could not access download directory",
            'severity': 'error',
            'description': "The server could not locate the directory where FHIR packages are stored."
        })
        logger.error("Validation failed: Could not access download directory")
        return result

    tgz_path = os.path.join(download_dir, construct_tgz_filename(package_name, version))
    logger.debug(f"Checking for package file: {tgz_path}")
    if not os.path.exists(tgz_path):
        result['valid'] = False
        result['errors'].append(f"Package file not found: {package_name}#{version}")
        result['details'].append({
            'issue': f"Package file not found: {package_name}#{version}",
            'severity': 'error',
            'description': f"The package {package_name}#{version} is not available in the download directory."
        })
        logger.error(f"Validation failed: Package file not found at {tgz_path}")
        return result

    # Use profile from meta.profile if available
    profile_url = None
    meta = resource.get('meta', {})
    profiles = meta.get('profile', [])
    if profiles:
        profile_url = profiles[0]
        logger.debug(f"Using profile from meta.profile: {profile_url}")

    # Find StructureDefinition
    sd_data, sd_path = find_and_extract_sd(tgz_path, resource.get('resourceType'), profile_url)
    if not sd_data and include_dependencies:
        logger.debug(f"SD not found in {package_name}#{version}. Checking dependencies.")
        try:
            with tarfile.open(tgz_path, "r:gz") as tar:
                package_json_member = None
                for member in tar:
                    if member.name == 'package/package.json':
                        package_json_member = member
                        break
                if package_json_member:
                    fileobj = tar.extractfile(package_json_member)
                    pkg_data = json.load(fileobj)
                    fileobj.close()
                    dependencies = pkg_data.get('dependencies', {})
                    logger.debug(f"Found dependencies: {dependencies}")
                    for dep_name, dep_version in dependencies.items():
                        dep_tgz = os.path.join(download_dir, construct_tgz_filename(dep_name, dep_version))
                        if os.path.exists(dep_tgz):
                            logger.debug(f"Searching SD in dependency {dep_name}#{dep_version}")
                            sd_data, sd_path = find_and_extract_sd(dep_tgz, resource.get('resourceType'), profile_url)
                            if sd_data:
                                logger.info(f"Found SD in dependency {dep_name}#{dep_version} at {sd_path}")
                                break
                        else:
                            logger.warning(f"Dependency package {dep_name}#{dep_version} not found at {dep_tgz}")
                else:
                    logger.warning(f"No package.json found in {tgz_path}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse package.json in {tgz_path}: {e}")
        except tarfile.TarError as e:
            logger.error(f"Failed to read {tgz_path} while checking dependencies: {e}")
        except Exception as e:
            logger.error(f"Unexpected error while checking dependencies in {tgz_path}: {e}")

    if not sd_data:
        result['valid'] = False
        result['errors'].append(f"No StructureDefinition found for {resource.get('resourceType')} with profile {profile_url or 'any'}")
        result['details'].append({
            'issue': f"No StructureDefinition found for {resource.get('resourceType')} with profile {profile_url or 'any'}",
            'severity': 'error',
            'description': f"The package {package_name}#{version} (and dependencies, if checked) does not contain a matching StructureDefinition."
        })
        logger.error(f"Validation failed: No SD for {resource.get('resourceType')} in {tgz_path}")
        return result
    logger.debug(f"Found SD at {sd_path}")

    # Validate required elements (min=1)
    errors = []
    warnings = set()  # Deduplicate warnings
    elements = sd_data.get('snapshot', {}).get('element', [])
    for element in elements:
        path = element.get('path')
        min_val = element.get('min', 0)
        must_support = element.get('mustSupport', False)
        definition = element.get('definition', 'No definition provided in StructureDefinition.')

        # Check required elements
        if min_val > 0 and not '.' in path[1 + path.find('.'):] if path.find('.') != -1 else True:
            value = navigate_fhir_path(resource, path)
            if value is None or (isinstance(value, list) and not any(value)):
                error_msg = f"{resource.get('resourceType')}/{resource.get('id', 'unknown')}: Required element {path} missing"
                errors.append(error_msg)
                result['details'].append({
                    'issue': error_msg,
                    'severity': 'error',
                    'description': f"{definition} This element is mandatory (min={min_val}) per the profile {profile_url or 'unknown'}."
                })
                logger.info(f"Validation error: Required element {path} missing")

        # Check must-support elements
        if must_support and not '.' in path[1 + path.find('.'):] if path.find('.') != -1 else True:
            if '[x]' in path:
                base_path = path.replace('[x]', '')
                found = False
                for suffix in ['Quantity', 'CodeableConcept', 'String', 'DateTime', 'Period', 'Range']:
                    test_path = f"{base_path}{suffix}"
                    value = navigate_fhir_path(resource, test_path)
                    if value is not None and (not isinstance(value, list) or any(value)):
                        found = True
                        break
                if not found:
                    warning_msg = f"{resource.get('resourceType')}/{resource.get('id', 'unknown')}: Must Support element {path} missing or empty"
                    warnings.add(warning_msg)
                    result['details'].append({
                        'issue': warning_msg,
                        'severity': 'warning',
                        'description': f"{definition} This element is marked as Must Support in AU Core, meaning it should be populated if the data is available (e.g., phone or email for Patient.telecom)."
                    })
                    logger.info(f"Validation warning: Must Support element {path} missing or empty")
            else:
                value = navigate_fhir_path(resource, path)
                if value is None or (isinstance(value, list) and not any(value)):
                    if element.get('min', 0) == 0:
                        warning_msg = f"{resource.get('resourceType')}/{resource.get('id', 'unknown')}: Must Support element {path} missing or empty"
                        warnings.add(warning_msg)
                        result['details'].append({
                            'issue': warning_msg,
                            'severity': 'warning',
                            'description': f"{definition} This element is marked as Must Support in AU Core, meaning it should be populated if the data is available (e.g., phone or email for Patient.telecom)."
                        })
                        logger.info(f"Validation warning: Must Support element {path} missing or empty")

        # Handle dataAbsentReason for must-support elements
        if path.endswith('dataAbsentReason') and must_support:
            value_x_path = path.replace('dataAbsentReason', 'value[x]')
            value_found = False
            for suffix in ['Quantity', 'CodeableConcept', 'String', 'DateTime', 'Period', 'Range']:
                test_path = path.replace('dataAbsentReason', f'value{suffix}')
                value = navigate_fhir_path(resource, test_path)
                if value is not None and (not isinstance(value, list) or any(value)):
                    value_found = True
                    break
            if not value_found:
                value = navigate_fhir_path(resource, path)
                if value is None or (isinstance(value, list) and not any(value)):
                    warning_msg = f"{resource.get('resourceType')}/{resource.get('id', 'unknown')}: Must Support element {path} missing or empty"
                    warnings.add(warning_msg)
                    result['details'].append({
                        'issue': warning_msg,
                        'severity': 'warning',
                        'description': f"{definition} This element is marked as Must Support and should be used to indicate why the associated value is absent."
                    })
                    logger.info(f"Validation warning: Must Support element {path} missing or empty")

    result['errors'] = errors
    result['warnings'] = list(warnings)
    result['valid'] = len(errors) == 0
    result['summary'] = {
        'error_count': len(errors),
        'warning_count': len(warnings)
    }
    logger.debug(f"Validation result: valid={result['valid']}, errors={len(result['errors'])}, warnings={len(result['warnings'])}")
    return result

def validate_resource_against_profile(package_name, version, resource, include_dependencies=True):
    result = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'details': [],
        'resource_type': resource.get('resourceType'),
        'resource_id': resource.get('id', 'unknown'),
        'profile': resource.get('meta', {}).get('profile', [None])[0]
    }

    # Attempt HAPI validation if a profile is specified
    if result['profile']:
        try:
            hapi_url = f"http://localhost:8080/fhir/{resource['resourceType']}/$validate?profile={result['profile']}"
            response = requests.post(
                hapi_url,
                json=resource,
                headers={'Content-Type': 'application/fhir+json', 'Accept': 'application/fhir+json'},
                timeout=10
            )
            response.raise_for_status()
            outcome = response.json()
            if outcome.get('resourceType') == 'OperationOutcome':
                for issue in outcome.get('issue', []):
                    severity = issue.get('severity')
                    diagnostics = issue.get('diagnostics', issue.get('details', {}).get('text', 'No details provided'))
                    detail = {
                        'issue': diagnostics,
                        'severity': severity,
                        'description': issue.get('details', {}).get('text', diagnostics)
                    }
                    if severity in ['error', 'fatal']:
                        result['valid'] = False
                        result['errors'].append(diagnostics)
                    elif severity == 'warning':
                        result['warnings'].append(diagnostics)
                    result['details'].append(detail)
                result['summary'] = {
                    'error_count': len(result['errors']),
                    'warning_count': len(result['warnings'])
                }
                logger.debug(f"HAPI validation for {result['resource_type']}/{result['resource_id']}: valid={result['valid']}, errors={len(result['errors'])}, warnings={len(result['warnings'])}")
                return result
            else:
                logger.warning(f"HAPI returned non-OperationOutcome: {outcome.get('resourceType')}")
        except requests.RequestException as e:
            logger.error(f"HAPI validation failed for {result['resource_type']}/{result['resource_id']}: {e}")
            result['details'].append({
                'issue': f"HAPI validation failed: {str(e)}",
                'severity': 'warning',
                'description': 'Falling back to local validation due to HAPI server error.'
            })

    # Fallback to local validation
    download_dir = _get_download_dir()
    if not download_dir:
        result['valid'] = False
        result['errors'].append("Could not access download directory")
        result['details'].append({
            'issue': "Could not access download directory",
            'severity': 'error',
            'description': "The server could not locate the directory where FHIR packages are stored."
        })
        return result

    tgz_path = os.path.join(download_dir, construct_tgz_filename(package_name, version))
    sd_data, sd_path = find_and_extract_sd(tgz_path, resource.get('resourceType'), result['profile'])
    if not sd_data:
        result['valid'] = False
        result['errors'].append(f"No StructureDefinition found for {resource.get('resourceType')}")
        result['details'].append({
            'issue': f"No StructureDefinition found for {resource.get('resourceType')}",
            'severity': 'error',
            'description': f"The package {package_name}#{version} does not contain a matching StructureDefinition."
        })
        return result

    elements = sd_data.get('snapshot', {}).get('element', [])
    for element in elements:
        path = element.get('path')
        min_val = element.get('min', 0)
        must_support = element.get('mustSupport', False)
        slicing = element.get('slicing')
        slice_name = element.get('sliceName')

        # Check required elements
        if min_val > 0:
            value = navigate_fhir_path(resource, path)
            if value is None or (isinstance(value, list) and not any(value)):
                result['valid'] = False
                result['errors'].append(f"Required element {path} missing")
                result['details'].append({
                    'issue': f"Required element {path} missing",
                    'severity': 'error',
                    'description': f"Element {path} has min={min_val} in profile {result['profile'] or 'unknown'}"
                })

        # Check must-support elements
        if must_support:
            value = navigate_fhir_path(resource, slice_name if slice_name else path)
            if value is None or (isinstance(value, list) and not any(value)):
                result['warnings'].append(f"Must Support element {path} missing or empty")
                result['details'].append({
                    'issue': f"Must Support element {path} missing or empty",
                    'severity': 'warning',
                    'description': f"Element {path} is marked as Must Support in profile {result['profile'] or 'unknown'}"
                })

        # Validate slicing
        if slicing and not slice_name:  # Parent slicing element
            discriminator = slicing.get('discriminator', [])
            for d in discriminator:
                d_type = d.get('type')
                d_path = d.get('path')
                if d_type == 'value':
                    sliced_elements = navigate_fhir_path(resource, path)
                    if isinstance(sliced_elements, list):
                        seen_values = set()
                        for elem in sliced_elements:
                            d_value = navigate_fhir_path(elem, d_path)
                            if d_value in seen_values:
                                result['valid'] = False
                                result['errors'].append(f"Duplicate discriminator value {d_value} for {path}.{d_path}")
                            seen_values.add(d_value)
                elif d_type == 'type':
                    sliced_elements = navigate_fhir_path(resource, path)
                    if isinstance(sliced_elements, list):
                        for elem in sliced_elements:
                            if not navigate_fhir_path(elem, d_path):
                                result['valid'] = False
                                result['errors'].append(f"Missing discriminator type {d_path} for {path}")

    result['summary'] = {
        'error_count': len(result['errors']),
        'warning_count': len(result['warnings'])
    }
    return result

def validate_bundle_against_profile(package_name, version, bundle, include_dependencies=True):
    """Validates a FHIR Bundle against profiles in the specified package."""
    logger.debug(f"Validating bundle against {package_name}#{version}, include_dependencies={include_dependencies}")
    result = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'details': [],
        'results': {},
        'summary': {
            'resource_count': 0,
            'failed_resources': 0,
            'profiles_validated': set()
        }
    }
    if not bundle.get('resourceType') == 'Bundle':
        result['valid'] = False
        result['errors'].append("Resource is not a Bundle")
        result['details'].append({
            'issue': "Resource is not a Bundle",
            'severity': 'error',
            'description': "The provided resource must have resourceType 'Bundle' to be validated as a bundle."
        })
        logger.error("Validation failed: Resource is not a Bundle")
        return result

    # Track references to validate resolvability
    references = set()
    resolved_references = set()

    for entry in bundle.get('entry', []):
        resource = entry.get('resource')
        if not resource:
            continue
        resource_type = resource.get('resourceType')
        resource_id = resource.get('id', 'unknown')
        result['summary']['resource_count'] += 1

        # Collect references
        for key, value in resource.items():
            if isinstance(value, dict) and 'reference' in value:
                references.add(value['reference'])
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and 'reference' in item:
                        references.add(item['reference'])

        # Validate resource
        validation_result = validate_resource_against_profile(package_name, version, resource, include_dependencies)
        result['results'][f"{resource_type}/{resource_id}"] = validation_result
        result['summary']['profiles_validated'].add(validation_result['profile'] or 'unknown')

        # Aggregate errors and warnings
        if not validation_result['valid']:
            result['valid'] = False
            result['summary']['failed_resources'] += 1
        result['errors'].extend(validation_result['errors'])
        result['warnings'].extend(validation_result['warnings'])
        result['details'].extend(validation_result['details'])

        # Mark resource as resolved if it has an ID
        if resource_id != 'unknown':
            resolved_references.add(f"{resource_type}/{resource_id}")

    # Check for unresolved references
    unresolved = references - resolved_references
    for ref in unresolved:
        warning_msg = f"Unresolved reference: {ref}"
        result['warnings'].append(warning_msg)
        result['details'].append({
            'issue': warning_msg,
            'severity': 'warning',
            'description': f"The reference {ref} points to a resource not included in the bundle. Ensure the referenced resource is present or resolvable."
        })
        logger.info(f"Validation warning: Unresolved reference {ref}")

    # Finalize summary
    result['summary']['profiles_validated'] = list(result['summary']['profiles_validated'])
    result['summary']['error_count'] = len(result['errors'])
    result['summary']['warning_count'] = len(result['warnings'])
    logger.debug(f"Bundle validation result: valid={result['valid']}, errors={result['summary']['error_count']}, warnings={result['summary']['warning_count']}, resources={result['summary']['resource_count']}")
    return result

# --- Structure Definition Retrieval ---
def get_structure_definition(package_name, version, resource_type):
    """Fetches StructureDefinition with slicing support."""
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Could not get download directory.")
        return {'error': 'Download directory not accessible'}

    tgz_filename = construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(download_dir, tgz_filename)
    sd_data, sd_path = find_and_extract_sd(tgz_path, resource_type)

    if not sd_data:
        # Fallback to canonical package
        canonical_tgz = construct_tgz_filename(*CANONICAL_PACKAGE)
        canonical_path = os.path.join(download_dir, canonical_tgz)
        sd_data, sd_path = find_and_extract_sd(canonical_path, resource_type)
        if sd_data:
            logger.info(f"Using canonical SD for {resource_type} from {canonical_path}")
            elements = sd_data.get('snapshot', {}).get('element', [])
            return {
                'elements': elements,
                'must_support_paths': [el['path'] for el in elements if el.get('mustSupport', False)],
                'slices': [],
                'fallback_used': True,
                'source_package': f"{CANONICAL_PACKAGE[0]}#{CANONICAL_PACKAGE[1]}"
            }
        logger.error(f"No StructureDefinition found for {resource_type} in {package_name}#{version} or canonical package")
        return {'error': f"No StructureDefinition for {resource_type}"}

    elements = sd_data.get('snapshot', {}).get('element', [])
    must_support_paths = []
    slices = []

    # Process elements for must-support and slicing
    for element in elements:
        path = element.get('path', '')
        element_id = element.get('id', '')
        slice_name = element.get('sliceName')
        if element.get('mustSupport', False):
            ms_path = f"{path}[sliceName='{slice_name}']" if slice_name else element_id
            must_support_paths.append(ms_path)
        if 'slicing' in element:
            slice_info = {
                'path': path,
                'sliceName': slice_name,
                'discriminator': element.get('slicing', {}).get('discriminator', []),
                'nested_slices': []
            }
            # Find nested slices
            for sub_element in elements:
                if sub_element['path'].startswith(path + '.') and 'slicing' in sub_element:
                    sub_slice_name = sub_element.get('sliceName')
                    slice_info['nested_slices'].append({
                        'path': sub_element['path'],
                        'sliceName': sub_slice_name,
                        'discriminator': sub_element.get('slicing', {}).get('discriminator', [])
                    })
            slices.append(slice_info)

    logger.debug(f"StructureDefinition for {resource_type}: {len(elements)} elements, {len(must_support_paths)} must-support paths, {len(slices)} slices")
    return {
        'elements': elements,
        'must_support_paths': sorted(list(set(must_support_paths))),
        'slices': slices,
        'fallback_used': False
    }

# --- Other Service Functions ---
def _build_package_index(download_dir):
    """Builds an index of canonical URLs to package details from .index.json files."""
    index = {}
    try:
        for tgz_file in os.listdir(download_dir):
            if not tgz_file.endswith('.tgz'):
                continue
            tgz_path = os.path.join(download_dir, tgz_file)
            try:
                with tarfile.open(tgz_path, "r:gz") as tar:
                    index_file = next((m for m in tar.getmembers() if m.name == 'package/.index.json'), None)
                    if index_file:
                        fileobj = tar.extractfile(index_file)
                        if fileobj:
                            content = json.loads(fileobj.read().decode('utf-8-sig'))
                            package_name = content.get('package-id', '')
                            package_version = content.get('version', '')
                            for file_entry in content.get('files', []):
                                canonical = file_entry.get('canonical')
                                filename = file_entry.get('filename')
                                if canonical and filename:
                                    index[canonical] = {
                                        'package_name': package_name,
                                        'package_version': package_version,
                                        'filename': filename
                                    }
                            fileobj.close()
            except Exception as e:
                logger.warning(f"Failed to index {tgz_file}: {e}")
    except Exception as e:
        logger.error(f"Error building package index: {e}")
    return index

def _find_definition_details(url, download_dir):
    """Finds package details for a canonical URL."""
    index = current_app.config.get('PACKAGE_INDEX')
    if index is None:
        index = _build_package_index(download_dir)
        current_app.config['PACKAGE_INDEX'] = index
    return index.get(url)

def _load_definition(details, download_dir):
    """Loads a StructureDefinition from package details."""
    if not details:
        return None
    tgz_path = os.path.join(download_dir, construct_tgz_filename(details['package_name'], details['package_version']))
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            member_path = f"package/{details['filename']}"
            member = next((m for m in tar.getmembers() if m.name == member_path), None)
            if member:
                fileobj = tar.extractfile(member)
                if fileobj:
                    data = json.loads(fileobj.read().decode('utf-8-sig'))
                    fileobj.close()
                    return data
    except Exception as e:
        logger.error(f"Failed to load definition {details['filename']} from {tgz_path}: {e}")
    return None

def download_package(name, version):
    """Downloads a single FHIR package."""
    download_dir = _get_download_dir()
    if not download_dir: return None, "Download dir error"
    filename = construct_tgz_filename(name, version)
    if not filename: return None, "Filename construction error"
    save_path = os.path.join(download_dir, filename)
    if os.path.exists(save_path):
        logger.info(f"Package already exists: {save_path}")
        return save_path, None
    package_url = f"{FHIR_REGISTRY_BASE_URL}/{name}/{version}"
    try:
        with requests.get(package_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        logger.info(f"Downloaded {filename}")
        return save_path, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Download failed for {name}#{version}: {e}")
        return None, f"Download error: {e}"
    except IOError as e:
        logger.error(f"File write error for {save_path}: {e}")
        return None, f"File write error: {e}"

def extract_dependencies(tgz_path):
    """Extracts dependencies from package.json."""
    package_json_path = "package/package.json"
    dependencies = {}
    error_message = None
    if not tgz_path or not os.path.exists(tgz_path): return None, "File not found"
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            try:
                pkg_member = tar.getmember(package_json_path)
                with tar.extractfile(pkg_member) as f:
                    pkg_data = json.load(f)
                    dependencies = pkg_data.get('dependencies', {})
            except KeyError: error_message = "package.json not found"
            except (json.JSONDecodeError, tarfile.TarError) as e: error_message = f"Error reading package.json: {e}"
    except tarfile.TarError as e: error_message = f"Error opening tarfile: {e}"
    except Exception as e: error_message = f"Unexpected error: {e}"
    return dependencies, error_message

def extract_used_types(tgz_path):
    """Extracts all resource types and referenced types from the package resources."""
    used_types = set()
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"Cannot extract used types: File not found at {tgz_path}")
        return used_types
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
                        if not isinstance(data, dict): continue
                        resource_type = data.get('resourceType')
                        if not resource_type: continue
                        used_types.add(resource_type)
                        if resource_type == 'StructureDefinition':
                            sd_type = data.get('type')
                            if sd_type: used_types.add(sd_type)
                            base_def = data.get('baseDefinition')
                            if base_def:
                                base_type = base_def.split('/')[-1]
                                if base_type and base_type[0].isupper(): used_types.add(base_type)
                            elements = data.get('snapshot', {}).get('element', []) or data.get('differential', {}).get('element', [])
                            for element in elements:
                                if isinstance(element, dict) and 'type' in element:
                                    for t in element.get('type', []):
                                        code = t.get('code')
                                        if code and code[0].isupper(): used_types.add(code)
                                        for profile_uri in t.get('targetProfile', []):
                                            if profile_uri:
                                                profile_type = profile_uri.split('/')[-1]
                                                if profile_type and profile_type[0].isupper(): used_types.add(profile_type)
                        else:
                            profiles = data.get('meta', {}).get('profile', [])
                            for profile_uri in profiles:
                                if profile_uri:
                                    profile_type = profile_uri.split('/')[-1]
                                    if profile_type and profile_type[0].isupper(): used_types.add(profile_type)
                            if resource_type == 'ValueSet':
                                for include in data.get('compose', {}).get('include', []):
                                    system = include.get('system')
                                    if system and system.startswith('http://hl7.org/fhir/'):
                                        type_name = system.split('/')[-1]
                                        if type_name and type_name[0].isupper() and not type_name.startswith('sid'):
                                            used_types.add(type_name)
                            if resource_type == 'CapabilityStatement':
                                for rest_item in data.get('rest', []):
                                    for resource_item in rest_item.get('resource', []):
                                        res_type = resource_item.get('type')
                                        if res_type and res_type[0].isupper(): used_types.add(res_type)
                                        profile_uri = resource_item.get('profile')
                                        if profile_uri:
                                            profile_type = profile_uri.split('/')[-1]
                                            if profile_type and profile_type[0].isupper(): used_types.add(profile_type)
                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse JSON in {member.name}: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode {member.name}: {e}")
                except Exception as e:
                    logger.warning(f"Could not process member {member.name}: {e}")
                finally:
                    if fileobj:
                        fileobj.close()
    except tarfile.ReadError as e:
        logger.error(f"Tar ReadError extracting used types from {tgz_path}: {e}")
    except tarfile.TarError as e:
        logger.error(f"TarError extracting used types from {tgz_path}: {e}")
    except FileNotFoundError:
        logger.error(f"Package file not found: {tgz_path}")
    except Exception as e:
        logger.error(f"Error extracting used types from {tgz_path}: {e}", exc_info=True)
    core_non_resource_types = {
        'string', 'boolean', 'integer', 'decimal', 'uri', 'url', 'canonical', 'base64Binary', 'instant',
        'date', 'dateTime', 'time', 'code', 'oid', 'id', 'markdown', 'unsignedInt', 'positiveInt', 'xhtml',
        'Element', 'BackboneElement', 'Resource', 'DomainResource', 'DataType'
    }
    final_used_types = {t for t in used_types if t not in core_non_resource_types and t[0].isupper()}
    logger.debug(f"Extracted used types from {os.path.basename(tgz_path)}: {final_used_types}")
    return final_used_types

def map_types_to_packages(used_types, all_dependencies, download_dir):
    """Maps used types to packages by checking .index.json files."""
    type_to_package = {}
    processed_types = set()
    for (pkg_name, pkg_version), _ in all_dependencies.items():
        tgz_filename = construct_tgz_filename(pkg_name, pkg_version)
        tgz_path = os.path.join(download_dir, tgz_filename)
        if not os.path.exists(tgz_path):
            logger.warning(f"Package {tgz_filename} not found for type mapping")
            continue
        try:
            with tarfile.open(tgz_path, "r:gz") as tar:
                index_file = next((m for m in tar.getmembers() if m.name == 'package/.index.json'), None)
                if index_file:
                    fileobj = tar.extractfile(index_file)
                    if fileobj:
                        content = json.loads(fileobj.read().decode('utf-8-sig'))
                        for file_entry in content.get('files', []):
                            resource_type = file_entry.get('resourceType')
                            filename = file_entry.get('filename')
                            if resource_type == 'StructureDefinition' and filename.endswith('.json'):
                                sd_name = os.path.splitext(os.path.basename(filename))[0]
                                if sd_name in used_types:
                                    type_to_package[sd_name] = (pkg_name, pkg_version)
                                    processed_types.add(sd_name)
                                    logger.debug(f"Mapped type '{sd_name}' to package '{pkg_name}#{pkg_version}'")
        except Exception as e:
            logger.warning(f"Failed to process .index.json for {pkg_name}#{pkg_version}: {e}")
    for t in used_types - processed_types:
        for (pkg_name, pkg_version), _ in all_dependencies.items():
            if t.lower() in pkg_name.lower():
                type_to_package[t] = (pkg_name, pkg_version)
                processed_types.add(t)
                logger.debug(f"Fallback: Mapped type '{t}' to package '{pkg_name}#{pkg_version}'")
                break
    canonical_name, canonical_version = CANONICAL_PACKAGE
    for t in used_types - processed_types:
        type_to_package[t] = CANONICAL_PACKAGE
        logger.debug(f"Fallback: Mapped type '{t}' to canonical package {canonical_name}#{canonical_version}")
    logger.debug(f"Final type-to-package mapping: {type_to_package}")
    return type_to_package

def import_package_and_dependencies(initial_name, initial_version, dependency_mode='recursive'):
    """Orchestrates recursive download and dependency extraction."""
    logger.info(f"Starting import for {initial_name}#{initial_version} with dependency_mode={dependency_mode}")
    download_dir = _get_download_dir()
    if not download_dir:
        return {'requested': (initial_name, initial_version), 'processed': set(), 'downloaded': {}, 'all_dependencies': {}, 'dependencies': [], 'errors': ['Download directory not accessible']}

    results = {
        'requested': (initial_name, initial_version),
        'processed': set(),
        'downloaded': {},
        'all_dependencies': {},
        'dependencies': [],
        'errors': []
    }
    pending_queue = [(initial_name, initial_version)]
    queued_or_processed_lookup = set([(initial_name, initial_version)])
    all_found_dependencies = set()

    while pending_queue:
        name, version = pending_queue.pop(0)
        package_id_tuple = (name, version)
        if package_id_tuple in results['processed']:
            logger.debug(f"Skipping already processed package: {name}#{version}")
            continue
        logger.info(f"Processing package from queue: {name}#{version}")
        save_path, dl_error = download_package(name, version)
        if dl_error:
            results['errors'].append(f"Download failed for {name}#{version}: {dl_error}")
            continue
        results['downloaded'][package_id_tuple] = save_path
        dependencies, dep_error = extract_dependencies(save_path)
        if dep_error:
            results['errors'].append(f"Dependency extraction failed for {name}#{version}: {dep_error}")
            results['processed'].add(package_id_tuple)
            continue
        elif dependencies is None:
            results['errors'].append(f"Dependency extraction returned critical error for {name}#{version}.")
            results['processed'].add(package_id_tuple)
            continue
        results['all_dependencies'][package_id_tuple] = dependencies
        results['processed'].add(package_id_tuple)
        current_package_deps = []
        for dep_name, dep_version in dependencies.items():
            if isinstance(dep_name, str) and isinstance(dep_version, str) and dep_name and dep_version:
                dep_tuple = (dep_name, dep_version)
                current_package_deps.append({"name": dep_name, "version": dep_version})
                if dep_tuple not in all_found_dependencies:
                    all_found_dependencies.add(dep_tuple)
                    results['dependencies'].append({"name": dep_name, "version": dep_version})
                if dep_tuple not in queued_or_processed_lookup:
                    should_queue = False
                    if dependency_mode == 'recursive':
                        should_queue = True
                    elif dependency_mode == 'patch-canonical' and dep_tuple == CANONICAL_PACKAGE:
                        should_queue = True
                    if should_queue:
                        logger.debug(f"Adding dependency to queue ({dependency_mode}): {dep_name}#{dep_version}")
                        pending_queue.append(dep_tuple)
                        queued_or_processed_lookup.add(dep_tuple)
        save_package_metadata(name, version, dependency_mode, current_package_deps)
        if dependency_mode == 'tree-shaking' and package_id_tuple == (initial_name, initial_version):
            logger.info(f"Performing tree-shaking for {initial_name}#{initial_version}")
            used_types = extract_used_types(save_path)
            if used_types:
                type_to_package = map_types_to_packages(used_types, results['all_dependencies'], download_dir)
                tree_shaken_deps = set(type_to_package.values()) - {package_id_tuple}
                if CANONICAL_PACKAGE not in tree_shaken_deps:
                    tree_shaken_deps.add(CANONICAL_PACKAGE)
                    logger.debug(f"Ensuring canonical package {CANONICAL_PACKAGE} for tree-shaking")
                for dep_tuple in tree_shaken_deps:
                    if dep_tuple not in queued_or_processed_lookup:
                        logger.info(f"Queueing tree-shaken dependency: {dep_tuple[0]}#{dep_tuple[1]}")
                        pending_queue.append(dep_tuple)
                        queued_or_processed_lookup.add(dep_tuple)
    results['dependencies'] = [{"name": d[0], "version": d[1]} for d in all_found_dependencies]
    logger.info(f"Import finished for {initial_name}#{initial_version}. Processed: {len(results['processed'])}, Downloaded: {len(results['downloaded'])}, Errors: {len(results['errors'])}")
    return results

# --- Validation Route ---
@services_bp.route('/validate-sample', methods=['POST'])
def validate_sample():
    """Validates a FHIR sample against a package profile."""
    logger.debug("Received validate-sample request")
    data = request.get_json(silent=True)
    if not data:
        logger.error("No JSON data provided or invalid JSON in validate-sample request")
        return jsonify({
            'valid': False,
            'errors': ["No JSON data provided or invalid JSON"],
            'warnings': [],
            'results': {}
        }), 400

    package_name = data.get('package_name')
    version = data.get('version')
    sample_data = data.get('sample_data')

    logger.debug(f"Request params: package_name={package_name}, version={version}, sample_data_length={len(sample_data) if sample_data else 0}")
    if not package_name or not version or not sample_data:
        logger.error(f"Missing required fields: package_name={package_name}, version={version}, sample_data={'provided' if sample_data else 'missing'}")
        return jsonify({
            'valid': False,
            'errors': ["Missing required fields: package_name, version, or sample_data"],
            'warnings': [],
            'results': {}
        }), 400

    # Verify download directory access
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Cannot access download directory")
        return jsonify({
            'valid': False,
            'errors': ["Server configuration error: cannot access package directory"],
            'warnings': [],
            'results': {}
        }), 500

    # Verify package file exists
    tgz_filename = construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(download_dir, tgz_filename)
    logger.debug(f"Checking package file: {tgz_path}")
    if not os.path.exists(tgz_path):
        logger.error(f"Package file not found: {tgz_path}")
        return jsonify({
            'valid': False,
            'errors': [f"Package not found: {package_name}#{version}. Please import the package first."],
            'warnings': [],
            'results': {}
        }), 400

    try:
        # Parse JSON sample
        sample = json.loads(sample_data)
        resource_type = sample.get('resourceType')
        if not resource_type:
            logger.error("Sample JSON missing resourceType")
            return jsonify({
                'valid': False,
                'errors': ["Sample JSON missing resourceType"],
                'warnings': [],
                'results': {}
            }), 400

        logger.debug(f"Validating {resource_type} against {package_name}#{version}")
        # Validate resource or bundle
        if resource_type == 'Bundle':
            result = validate_bundle_against_profile(package_name, version, sample)
        else:
            result = validate_resource_against_profile(package_name, version, sample)

        logger.info(f"Validation result for {resource_type} against {package_name}#{version}: valid={result['valid']}, errors={len(result['errors'])}, warnings={len(result['warnings'])}")
        return jsonify(result)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in sample_data: {e}")
        return jsonify({
            'valid': False,
            'errors': [f"Invalid JSON: {str(e)}"],
            'warnings': [],
            'results': {}
        }), 400
    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        return jsonify({
            'valid': False,
            'errors': [f"Validation failed: {str(e)}"],
            'warnings': [],
            'results': {}
        }), 500

def run_gofsh(input_path, output_dir, output_style, log_level, fhir_version=None, fishing_trip=False, dependencies=None, indent_rules=False, meta_profile='only-one', alias_file=None, no_alias=False):
    """Run GoFSH with advanced options and return FSH output and optional comparison report."""
    # Use a temporary output directory for initial GoFSH run
    temp_output_dir = tempfile.mkdtemp()
    os.chmod(temp_output_dir, 0o777)
    
    cmd = ["gofsh", input_path, "-o", temp_output_dir, "-s", output_style, "-l", log_level]
    if fhir_version:
        cmd.extend(["-u", fhir_version])
    if dependencies:
        for dep in dependencies:
            cmd.extend(["--dependency", dep.strip()])
    if indent_rules:
        cmd.append("--indent")
    if no_alias:
        cmd.append("--no-alias")
    if alias_file:
        cmd.extend(["--alias-file", alias_file])
    if meta_profile != 'only-one':
        cmd.extend(["--meta-profile", meta_profile])
    
    # Set environment to disable TTY interactions
    env = os.environ.copy()
    env["NODE_NO_READLINE"] = "1"
    env["NODE_NO_INTERACTIVE"] = "1"
    env["TERM"] = "dumb"
    env["CI"] = "true"
    env["FORCE_COLOR"] = "0"
    env["NODE_ENV"] = "production"
    
    # Create a wrapper script in /tmp
    wrapper_script = "/tmp/gofsh_wrapper.sh"
    output_file = "/tmp/gofsh_output.log"
    try:
        with open(wrapper_script, 'w') as f:
            f.write("#!/bin/bash\n")
            # Redirect /dev/tty writes to /dev/null
            f.write("exec 3>/dev/null\n")
            f.write(" ".join([f'"{arg}"' for arg in cmd]) + f" </dev/null >{output_file} 2>&1\n")
        os.chmod(wrapper_script, 0o755)
        
        # Log the wrapper script contents for debugging
        with open(wrapper_script, 'r') as f:
            logger.debug(f"Wrapper script contents:\n{f.read()}")
    except Exception as e:
        logger.error(f"Failed to create wrapper script {wrapper_script}: {str(e)}", exc_info=True)
        return None, None, f"Failed to create wrapper script: {str(e)}"
    
    try:
        # Log directory contents before execution
        logger.debug(f"Temp output directory contents before GoFSH: {os.listdir(temp_output_dir)}")
        
        result = subprocess.run(
            [wrapper_script],
            check=True,
            env=env
        )
        # Read output from the log file
        with open(output_file, 'r', encoding='utf-8') as f:
            output = f.read()
        logger.debug(f"GoFSH output:\n{output}")
        
        # Prepare final output directory
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        os.chmod(output_dir, 0o777)
        
        # Copy .fsh files, sushi-config.yaml, and input JSON to final output directory
        copied_files = []
        for root, _, files in os.walk(temp_output_dir):
            for file in files:
                src_path = os.path.join(root, file)
                if file.endswith(".fsh") or file == "sushi-config.yaml":
                    relative_path = os.path.relpath(src_path, temp_output_dir)
                    dst_path = os.path.join(output_dir, relative_path)
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                    copied_files.append(relative_path)
        
        # Copy input JSON to final directory
        input_filename = os.path.basename(input_path)
        dst_input_path = os.path.join(output_dir, "input", input_filename)
        os.makedirs(os.path.dirname(dst_input_path), exist_ok=True)
        shutil.copy2(input_path, dst_input_path)
        copied_files.append(os.path.join("input", input_filename))
        
        # Create a minimal sushi-config.yaml if missing
        sushi_config_path = os.path.join(output_dir, "sushi-config.yaml")
        if not os.path.exists(sushi_config_path):
            minimal_config = {
                "id": "fhirflare.temp",
                "canonical": "http://fhirflare.org",
                "name": "FHIRFLARETempIG",
                "version": "0.1.0",
                "fhirVersion": fhir_version or "4.0.1",
                "FSHOnly": True,
                "dependencies": dependencies or []
            }
            with open(sushi_config_path, 'w') as f:
                json.dump(minimal_config, f, indent=2)
            copied_files.append("sushi-config.yaml")
        
        # Run GoFSH with --fshing-trip in a fresh temporary directory
        comparison_report = None
        if fishing_trip:
            fishing_temp_dir = tempfile.mkdtemp()
            os.chmod(fishing_temp_dir, 0o777)
            gofsh_fishing_cmd = ["gofsh", input_path, "-o", fishing_temp_dir, "-s", output_style, "-l", log_level, "--fshing-trip"]
            if fhir_version:
                gofsh_fishing_cmd.extend(["-u", fhir_version])
            if dependencies:
                for dep in dependencies:
                    gofsh_fishing_cmd.extend(["--dependency", dep.strip()])
            if indent_rules:
                gofsh_fishing_cmd.append("--indent")
            if no_alias:
                gofsh_fishing_cmd.append("--no-alias")
            if alias_file:
                gofsh_fishing_cmd.extend(["--alias-file", alias_file])
            if meta_profile != 'only-one':
                gofsh_fishing_cmd.extend(["--meta-profile", meta_profile])
            
            try:
                with open(wrapper_script, 'w') as f:
                    f.write("#!/bin/bash\n")
                    f.write("exec 3>/dev/null\n")
                    f.write("exec >/dev/null 2>&1\n")  # Suppress all output to /dev/tty
                    f.write(" ".join([f'"{arg}"' for arg in gofsh_fishing_cmd]) + f" </dev/null >{output_file} 2>&1\n")
                os.chmod(wrapper_script, 0o755)
                
                logger.debug(f"GoFSH fishing-trip wrapper script contents:\n{open(wrapper_script, 'r').read()}")
                
                result = subprocess.run(
                    [wrapper_script],
                    check=True,
                    env=env
                )
                with open(output_file, 'r', encoding='utf-8') as f:
                    fishing_output = f.read()
                logger.debug(f"GoFSH fishing-trip output:\n{fishing_output}")
                
                # Copy fshing-trip-comparison.html to final directory
                for root, _, files in os.walk(fishing_temp_dir):
                    for file in files:
                        if file.endswith(".html") and "fshing-trip-comparison" in file.lower():
                            src_path = os.path.join(root, file)
                            dst_path = os.path.join(output_dir, file)
                            shutil.copy2(src_path, dst_path)
                            copied_files.append(file)
                            with open(dst_path, 'r', encoding='utf-8') as f:
                                comparison_report = f.read()
            except subprocess.CalledProcessError as e:
                error_output = ""
                if os.path.exists(output_file):
                    with open(output_file, 'r', encoding='utf-8') as f:
                        error_output = f.read()
                logger.error(f"GoFSH fishing-trip failed: {error_output}")
                return None, None, f"GoFSH fishing-trip failed: {error_output}"
            finally:
                if os.path.exists(fishing_temp_dir):
                    shutil.rmtree(fishing_temp_dir, ignore_errors=True)
        
        # Read FSH files from final output directory
        fsh_content = []
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.endswith(".fsh"):
                    with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                        fsh_content.append(f.read())
        fsh_output = "\n\n".join(fsh_content)
        
        # Log copied files
        logger.debug(f"Copied files to final output directory: {copied_files}")
        
        logger.info(f"GoFSH executed successfully for {input_path}")
        return fsh_output, comparison_report, None
    except subprocess.CalledProcessError as e:
        error_output = ""
        if os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                error_output = f.read()
        logger.error(f"GoFSH failed: {error_output}")
        return None, None, f"GoFSH failed: {error_output}"
    except Exception as e:
        logger.error(f"Error running GoFSH: {str(e)}", exc_info=True)
        return None, None, f"Error running GoFSH: {str(e)}"
    finally:
        # Clean up temporary files
        if os.path.exists(wrapper_script):
            os.remove(wrapper_script)
        if os.path.exists(output_file):
            os.remove(output_file)
        if os.path.exists(temp_output_dir):
            shutil.rmtree(temp_output_dir, ignore_errors=True)

def process_fhir_input(input_mode, fhir_file, fhir_text, alias_file=None):
    """Process user input (file or text) and save to temporary files."""
    temp_dir = tempfile.mkdtemp()
    input_file = None
    alias_path = None
    
    try:
        if input_mode == 'file' and fhir_file:
            content = fhir_file.read().decode('utf-8')
            file_type = 'json' if content.strip().startswith('{') else 'xml'
            input_file = os.path.join(temp_dir, f"input.{file_type}")
            with open(input_file, 'w') as f:
                f.write(content)
        elif input_mode == 'text' and fhir_text:
            content = fhir_text.strip()
            file_type = 'json' if content.strip().startswith('{') else 'xml'
            input_file = os.path.join(temp_dir, f"input.{file_type}")
            with open(input_file, 'w') as f:
                f.write(content)
        else:
            return None, None, None, "No input provided"
        
        # Basic validation
        if file_type == 'json':
            try:
                json.loads(content)
            except json.JSONDecodeError:
                return None, None, None, "Invalid JSON format"
        elif file_type == 'xml':
            try:
                ET.fromstring(content)
            except ET.ParseError:
                return None, None, None, "Invalid XML format"
        
        # Process alias file if provided
        if alias_file:
            alias_content = alias_file.read().decode('utf-8')
            alias_path = os.path.join(temp_dir, "aliases.fsh")
            with open(alias_path, 'w') as f:
                f.write(alias_content)
        
        logger.debug(f"Processed input: {(input_file, alias_path)}")
        return input_file, temp_dir, alias_path, None
    except Exception as e:
        logger.error(f"Error processing input: {str(e)}", exc_info=True)
        return None, None, None, f"Error processing input: {str(e)}"

# --- Standalone Test ---
if __name__ == '__main__':
    logger.info("Running services.py directly for testing.")
    class MockFlask:
        class Config(dict):
            pass
        config = Config()
        instance_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'instance'))
    mock_app = MockFlask()
    test_download_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'instance', DOWNLOAD_DIR_NAME))
    mock_app.config['FHIR_PACKAGES_DIR'] = test_download_dir
    os.makedirs(test_download_dir, exist_ok=True)
    logger.info(f"Using test download directory: {test_download_dir}")
    print("\n--- Testing Filename Parsing ---")
    test_files = [
        "hl7.fhir.r4.core-4.0.1.tgz",
        "hl7.fhir.us.core-6.1.0.tgz",
        "fhir.myig.patient-1.2.3-beta.tgz",
        "my.company.fhir.Terminologies-0.1.0.tgz",
        "package-with-hyphens-in-name-1.0.tgz",
        "noversion.tgz",
        "badformat-1.0",
        "hl7.fhir.au.core-1.1.0-preview.tgz",
    ]
    for tf in test_files:
        p_name, p_ver = parse_package_filename(tf)
        print(f"'{tf}' -> Name: '{p_name}', Version: '{p_ver}'")
    pkg_name_to_test = "hl7.fhir.au.core"
    pkg_version_to_test = "1.1.0-preview"
    print(f"\n--- Testing Import: {pkg_name_to_test}#{pkg_version_to_test} ---")
    import_results = import_package_and_dependencies(pkg_name_to_test, pkg_version_to_test, dependency_mode='recursive')
    print("\nImport Results:")
    print(f"  Requested: {import_results['requested']}")
    print(f"  Downloaded Count: {len(import_results['downloaded'])}")
    print(f"  Unique Dependencies Found: {len(import_results['dependencies'])}")
    print(f"  Errors: {len(import_results['errors'])}")
    for error in import_results['errors']:
        print(f"    - {error}")
    if (pkg_name_to_test, pkg_version_to_test) in import_results['downloaded']:
        test_tgz_path = import_results['downloaded'][(pkg_name_to_test, pkg_version_to_test)]
        print(f"\n--- Testing Processing: {test_tgz_path} ---")
        processing_results = process_package_file(test_tgz_path)
        print("\nProcessing Results:")
        print(f"  Resource Types Info Count: {len(processing_results.get('resource_types_info', []))}")
        print(f"  Profiles with MS Elements: {sum(1 for r in processing_results.get('resource_types_info', []) if r.get('must_support'))}")
        print(f"  Optional Extensions w/ MS: {sum(1 for r in processing_results.get('resource_types_info', []) if r.get('optional_usage'))}")
        print(f"  Must Support Elements Dict Count: {len(processing_results.get('must_support_elements', {}))}")
        print(f"  Examples Dict Count: {len(processing_results.get('examples', {}))}")
        print(f"  Complies With Profiles: {processing_results.get('complies_with_profiles', [])}")
        print(f"  Imposed Profiles: {processing_results.get('imposed_profiles', [])}")
        print(f"  Processing Errors: {processing_results.get('errors', [])}")
    else:
        print(f"\n--- Skipping Processing Test (Import failed for {pkg_name_to_test}#{pkg_version_to_test}) ---")

