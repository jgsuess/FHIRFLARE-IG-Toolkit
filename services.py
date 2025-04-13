import requests
import os
import tarfile
import json
import re
import logging
from flask import current_app
from collections import defaultdict
from pathlib import Path
import datetime

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

# Define standard FHIR R4 base types (used selectively)
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
    """
    Parses a standard FHIR package filename (e.g., name-version.tgz)
    into package name and version. Handles common variations.
    Returns (name, version) tuple, or (None, None) if parsing fails.
    """
    name = None
    version = None
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
    logger.warning(f"Could not confidently parse version from '{filename}'. Treating '{base_name}' as name.")
    name = base_name.replace('_', '.')
    version = ""
    return name, version

def find_and_extract_sd(tgz_path, resource_identifier):
    """
    Helper to find and extract StructureDefinition json from a given tgz path.
    Matches by resource ID, Name, or Type defined within the SD file.
    Returns (sd_data, found_path_in_tar) or (None, None).
    """
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
                            sd_url = data.get('url')
                            if resource_identifier and (
                                (sd_id and resource_identifier.lower() == sd_id.lower()) or
                                (sd_name and resource_identifier.lower() == sd_name.lower()) or
                                (sd_type and resource_identifier.lower() == sd_type.lower()) or
                                (sd_url and resource_identifier.lower() == sd_url.lower()) or
                                (os.path.splitext(os.path.basename(member.name))[0].lower() == resource_identifier.lower())
                            ):
                                sd_data = data
                                found_path = member.name
                                logger.info(f"Found matching SD for '{resource_identifier}' at path: {found_path}")
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
            if sd_data is None:
                logger.info(f"SD matching identifier '{resource_identifier}' not found within archive {os.path.basename(tgz_path)}")
    except tarfile.ReadError as e:
        logger.error(f"Tar ReadError reading {tgz_path}: {e}")
        return None, None
    except tarfile.TarError as e:
        logger.error(f"TarError reading {tgz_path} in find_and_extract_sd: {e}")
        raise
    except FileNotFoundError:
        logger.error(f"FileNotFoundError reading {tgz_path} in find_and_extract_sd.")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in find_and_extract_sd for {tgz_path}: {e}", exc_info=True)
        raise
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

# --- Package Processing ---

def process_package_file(tgz_path):
    """Extracts types, profile status, MS elements, examples, and profile relationships from a downloaded .tgz package."""
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"Package file not found for processing: {tgz_path}")
        return {'errors': [f"Package file not found: {tgz_path}"], 'resource_types_info': []}

    pkg_basename = os.path.basename(tgz_path)
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
                                # Use id for sliced elements to ensure uniqueness
                                ms_path = element_id if slice_name else element_path
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

            # --- Pass 2: Process Examples ---
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

                        # Prioritize meta.profile matches (from oldest version for reliability)
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

                        # Fallback to base type
                        if not found_profile_match:
                            key_to_use = resource_type
                            if key_to_use not in resource_info:
                                resource_info[key_to_use].update({'name': key_to_use, 'type': resource_type, 'is_profile': False})
                            associated_key = key_to_use
                            logger.debug(f"Example {member.name} associated with resource type {associated_key}")
                        referenced_types.add(resource_type)
                    else:
                        # Handle non-JSON examples (from oldest version)
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

            # --- Pass 3: Ensure Relevant Base Types ---
            logger.debug("Ensuring relevant FHIR R4 base types...")
            essential_types = {'CapabilityStatement'}
            for type_name in referenced_types | essential_types:
                if type_name in FHIR_R4_BASE_TYPES and type_name not in resource_info:
                    resource_info[type_name]['name'] = type_name
                    resource_info[type_name]['type'] = type_name
                    resource_info[type_name]['is_profile'] = False
                    logger.debug(f"Added base type entry for {type_name}")

            # --- Final Consolidation ---
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
def navigate_fhir_path(resource, path, extension_url=None):
    """
    Navigates a FHIR resource using a FHIRPath-like expression.
    Returns the value(s) at the specified path or None if not found.
    """
    if not resource or not path:
        return None
    parts = path.split('.')
    current = resource
    for part in parts:
        # Handle array indexing (e.g., element[0])
        match = re.match(r'^(\w+)\[(\d+)\]$', part)
        if match:
            key, index = match.groups()
            index = int(index)
            if isinstance(current, dict) and key in current:
                if isinstance(current[key], list) and index < len(current[key]):
                    current = current[key][index]
                else:
                    return None
            else:
                return None
        else:
            # Handle regular path components
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
    # Handle extension filtering
    if extension_url and isinstance(current, list):
        current = [item for item in current if item.get('url') == extension_url]
    return current

def validate_resource_against_profile(package_name, version, resource, include_dependencies=True):
    """
    Validates a FHIR resource against a StructureDefinition in the specified package.
    Returns a dict with 'valid', 'errors', and 'warnings'.
    """
    logger.debug(f"Validating resource {resource.get('resourceType')} against {package_name}#{version}")
    result = {'valid': True, 'errors': [], 'warnings': []}
    download_dir = _get_download_dir()
    if not download_dir:
        result['valid'] = False
        result['errors'].append("Could not access download directory")
        return result

    tgz_path = os.path.join(download_dir, construct_tgz_filename(package_name, version))
    sd_data, _ = find_and_extract_sd(tgz_path, resource.get('resourceType'))
    if not sd_data:
        result['valid'] = False
        result['errors'].append(f"No StructureDefinition found for {resource.get('resourceType')} in {package_name}#{version}")
        return result

    # Basic validation: check required elements and Must Support
    elements = sd_data.get('snapshot', {}).get('element', [])
    for element in elements:
        if element.get('min', 0) > 0:
            path = element.get('path')
            value = navigate_fhir_path(resource, path)
            if value is None or (isinstance(value, list) and not value):
                result['valid'] = False
                result['errors'].append(f"Required element {path} missing")
        if element.get('mustSupport', False):
            path = element.get('path')
            value = navigate_fhir_path(resource, path)
            if value is None or (isinstance(value, list) and not value):
                result['warnings'].append(f"Must Support element {path} missing or empty")

    return result

def validate_bundle_against_profile(package_name, version, bundle, include_dependencies=True):
    """
    Validates a FHIR Bundle against profiles in the specified package.
    Returns a dict with 'valid', 'errors', 'warnings', and per-resource 'results'.
    """
    logger.debug(f"Validating bundle against {package_name}#{version}")
    result = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'results': {}
    }
    if not bundle.get('resourceType') == 'Bundle':
        result['valid'] = False
        result['errors'].append("Resource is not a Bundle")
        return result

    for entry in bundle.get('entry', []):
        resource = entry.get('resource')
        if not resource:
            continue
        resource_type = resource.get('resourceType')
        resource_id = resource.get('id', 'unknown')
        validation_result = validate_resource_against_profile(package_name, version, resource, include_dependencies)
        result['results'][f"{resource_type}/{resource_id}"] = validation_result
        if not validation_result['valid']:
            result['valid'] = False
        result['errors'].extend(validation_result['errors'])
        result['warnings'].extend(validation_result['warnings'])

    return result

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
    tgz_path = os.path.join(download_dir, _construct_tgz_filename(details['package_name'], details['package_version']))
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


def map_types_to_packages(used_types, all_dependencies, download_dir):
    """Maps used types to packages by checking .index.json files for exported types."""
    type_to_package = {}
    processed_types = set()
    
    # Load .index.json from each package to map types
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
                                    logger.debug(f"Mapped type '{sd_name}' to package '{pkg_name}#{pkg_version}' via .index.json")
        except Exception as e:
            logger.warning(f"Failed to process .index.json for {pkg_name}#{pkg_version}: {e}")

    # Fallback: Use heuristic matching for unmapped types
    for t in used_types - processed_types:
        for (pkg_name, pkg_version), _ in all_dependencies.items():
            if t.lower() in pkg_name.lower():
                type_to_package[t] = (pkg_name, pkg_version)
                processed_types.add(t)
                logger.debug(f"Fallback: Mapped type '{t}' to package '{pkg_name}#{pkg_version}' via name heuristic")
                break

    # Final fallback: Map remaining types to canonical package
    canonical_name, canonical_version = CANONICAL_PACKAGE
    for t in used_types - processed_types:
        type_to_package[t] = CANONICAL_PACKAGE
        logger.debug(f"Fallback: Mapped type '{t}' to canonical package {canonical_name}#{canonical_version}")

    logger.debug(f"Final type-to-package mapping: {type_to_package}")
    return type_to_package

def import_package_and_dependencies(initial_name, initial_version, dependency_mode='recursive'):
    """Orchestrates recursive download and dependency extraction based on the dependency mode."""
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
                    # Tree-shaking dependencies are handled post-loop for the initial package
                    if should_queue:
                        logger.debug(f"Adding dependency to queue ({dependency_mode}): {dep_name}#{dep_version}")
                        pending_queue.append(dep_tuple)
                        queued_or_processed_lookup.add(dep_tuple)

        save_package_metadata(name, version, dependency_mode, current_package_deps)

        # Tree-shaking: Process dependencies for the initial package
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


# --- Standalone Test/Example Usage ---
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