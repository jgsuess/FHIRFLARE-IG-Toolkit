import requests
import os
import tarfile
import json
import re
import logging
import shutil
import sqlite3
import feedparser
from flask import current_app, Blueprint, request, jsonify
from fhirpathpy import evaluate
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote, urlparse
from types import SimpleNamespace
import datetime
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from flasgger import swag_from # Import swag_from here

# Define Blueprint
services_bp = Blueprint('services', __name__)

# Configure logging
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
else:
    pass
logger = logging.getLogger(__name__)

# --- ADD fhir.resources imports ---
try:
    from fhir.resources import get_fhir_model_class
    from fhir.resources.fhirtypesvalidators import FHIRValidationError  # Updated import path
    FHIR_RESOURCES_AVAILABLE = True
    logger.info("fhir.resources library found. XML parsing will use this.")
except ImportError as e:
    FHIR_RESOURCES_AVAILABLE = False
    logger.warning(f"fhir.resources library failed to import. XML parsing will be basic and dependency analysis for XML may be incomplete. Error: {str(e)}")
    # Define dummy classes if library not found to avoid NameErrors later
    class FHIRValidationError(Exception): pass
    def get_fhir_model_class(resource_type): raise NotImplementedError("fhir.resources not installed")
except Exception as e:
    FHIR_RESOURCES_AVAILABLE = False
    logger.error(f"Unexpected error importing fhir.resources library: {str(e)}")
    class FHIRValidationError(Exception): pass
    def get_fhir_model_class(resource_type): raise NotImplementedError("fhir.resources not installed")
# --- END fhir.resources imports ---

# --- Check for optional 'packaging' library ---
try:
    import packaging.version as pkg_version
    HAS_PACKAGING_LIB = True
    logger.info("Optional 'packaging' library found. Using for robust version comparison.")
except ImportError:
    HAS_PACKAGING_LIB = False
    logger.warning("Optional 'packaging' library not found. Using basic string comparison for versions.")
    # Define a simple fallback class if packaging is missing
    class BasicVersion:
        def __init__(self, v_str): self.v_str = str(v_str)
        # Define comparison methods for sorting compatibility
        def __lt__(self, other): return self.v_str < str(other)
        def __gt__(self, other): return self.v_str > str(other)
        def __eq__(self, other): return self.v_str == str(other)
        def __le__(self, other): return self.v_str <= str(other)
        def __ge__(self, other): return self.v_str >= str(other)
        def __ne__(self, other): return self.v_str != str(other)
        def __str__(self): return self.v_str
    pkg_version = SimpleNamespace(parse=BasicVersion, InvalidVersion=ValueError) # Mock parse and InvalidVersion

# --- Constants ---
FHIR_REGISTRY_BASE_URL = "https://packages.fhir.org"
DOWNLOAD_DIR_NAME = "fhir_packages"
CANONICAL_PACKAGE = ("hl7.fhir.r4.core", "4.0.1")
CANONICAL_PACKAGE_ID = f"{CANONICAL_PACKAGE[0]}#{CANONICAL_PACKAGE[1]}"

# --- Define Canonical Types ---
CANONICAL_RESOURCE_TYPES = {
    "StructureDefinition", "ValueSet", "CodeSystem", "SearchParameter",
    "CapabilityStatement", "ImplementationGuide", "ConceptMap", "NamingSystem",
    "OperationDefinition", "MessageDefinition", "CompartmentDefinition",
    "GraphDefinition", "StructureMap", "Questionnaire"
}
# -----------------------------

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


# -------------------------------------------------------------------
#Helper function to support normalize:

def safe_parse_version(v_str):
    """
    Attempts to parse a version string using packaging.version.
    Handles common FHIR suffixes like -dev, -ballot, -draft, -preview
    by treating them as standard pre-releases (-a0, -b0, -rc0) for comparison.
    Returns a comparable Version object or a fallback for unparseable strings.
    """
    if not v_str or not isinstance(v_str, str):
        # Handle None or non-string input, treat as lowest possible version
        return pkg_version.parse("0.0.0a0") # Use alpha pre-release

    # Try standard parsing first
    try:
        return pkg_version.parse(v_str)
    except pkg_version.InvalidVersion:
        # Handle common FHIR suffixes if standard parsing fails
        original_v_str = v_str # Keep original for logging
        v_str_norm = v_str.lower()
        # Split into base version and suffix
        base_part = v_str_norm
        suffix = None
        if '-' in v_str_norm:
            parts = v_str_norm.split('-', 1)
            base_part = parts[0]
            suffix = parts[1]

        # Check if base looks like a version number
        if re.match(r'^\d+(\.\d+)*$', base_part):
            try:
                # Map FHIR suffixes to PEP 440 pre-release types for sorting
                if suffix in ['dev', 'snapshot', 'ci-build']:
                    # Treat as alpha (earliest pre-release)
                    return pkg_version.parse(f"{base_part}a0")
                elif suffix in ['draft', 'ballot', 'preview']:
                    # Treat as beta (after alpha)
                    return pkg_version.parse(f"{base_part}b0")
                # Add more mappings if needed (e.g., -rc -> rc0)
                elif suffix and suffix.startswith('rc'):
                     rc_num = ''.join(filter(str.isdigit, suffix)) or '0'
                     return pkg_version.parse(f"{base_part}rc{rc_num}")

                # If suffix isn't recognized, still try parsing base as final/post
                # This might happen for odd suffixes like -final (though unlikely)
                # If base itself parses, use that (treats unknown suffix as > pre-release)
                return pkg_version.parse(base_part)

            except pkg_version.InvalidVersion:
                # If base_part itself is invalid after splitting
                 logger.warning(f"Invalid base version '{base_part}' after splitting '{original_v_str}'. Treating as alpha.")
                 return pkg_version.parse("0.0.0a0")
            except Exception as e:
                 logger.error(f"Unexpected error parsing FHIR-suffixed version '{original_v_str}': {e}")
                 return pkg_version.parse("0.0.0a0")
        else:
            # Base part doesn't look like numbers/dots (e.g., "current", "dev")
            logger.warning(f"Unparseable version '{original_v_str}' (base '{base_part}' not standard). Treating as alpha.")
            return pkg_version.parse("0.0.0a0") # Treat fully non-standard versions as very early

    except Exception as e:
         # Catch any other unexpected parsing errors
         logger.error(f"Unexpected error in safe_parse_version for '{v_str}': {e}")
         return pkg_version.parse("0.0.0a0") # Fallback

# --- MODIFIED FUNCTION with Enhanced Logging ---
def get_additional_registries():
    """Fetches the list of additional FHIR IG registries from the master feed."""
    logger.debug("Entering get_additional_registries function")
    feed_registry_url = 'https://raw.githubusercontent.com/FHIR/ig-registry/master/package-feeds.json'
    feeds = [] # Default to empty list
    try:
        logger.info(f"Attempting to fetch feed registry from {feed_registry_url}")
        # Use a reasonable timeout
        response = requests.get(feed_registry_url, timeout=15)
        logger.debug(f"Feed registry request to {feed_registry_url} returned status code: {response.status_code}")
        # Raise HTTPError for bad responses (4xx or 5xx)
        response.raise_for_status()

        # Log successful fetch
        logger.debug(f"Successfully fetched feed registry. Response text (first 500 chars): {response.text[:500]}...")

        try:
            # Attempt to parse JSON
            data = json.loads(response.text)
            feeds_raw = data.get('feeds', [])
            # Ensure structure is as expected before adding
            feeds = [{'name': feed['name'], 'url': feed['url']}
                     for feed in feeds_raw
                     if isinstance(feed, dict) and 'name' in feed and 'url' in feed]
            logger.info(f"Successfully parsed {len(feeds)} valid feeds from {feed_registry_url}")

        except json.JSONDecodeError as e:
            # Log JSON parsing errors specifically
            logger.error(f"JSON decoding error for feed registry from {feed_registry_url}: {e}")
            # Log the problematic text snippet to help diagnose
            logger.error(f"Problematic JSON text snippet: {response.text[:500]}...")
            # feeds remains []

    # --- Specific Exception Handling ---
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching feed registry from {feed_registry_url}: {e}", exc_info=True)
        # feeds remains []
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error fetching feed registry from {feed_registry_url}: {e}", exc_info=True)
        # feeds remains []
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout fetching feed registry from {feed_registry_url}: {e}", exc_info=True)
        # feeds remains []
    except requests.exceptions.RequestException as e:
        # Catch other potential request-related errors
        logger.error(f"General request error fetching feed registry from {feed_registry_url}: {e}", exc_info=True)
        # feeds remains []
    except Exception as e:
        # Catch any other unexpected errors during the process
        logger.error(f"Unexpected error fetching feed registry from {feed_registry_url}: {e}", exc_info=True)
        # feeds remains []

    logger.debug(f"Exiting get_additional_registries function, returning {len(feeds)} feeds.")
    return feeds
# --- END MODIFIED FUNCTION ---

def import_manual_package_and_dependencies(input_source, version=None, dependency_mode='recursive', is_file=False, is_url=False, resolve_dependencies=True):
    """
    Import a FHIR Implementation Guide package manually, cloning import_package_and_dependencies.
    Supports registry, file, or URL inputs with dependency handling.
    
    Args:
        input_source (str): Package name (for registry), file path (for file), or URL (for URL).
        version (str, optional): Package version for registry imports.
        dependency_mode (str): Dependency import mode ('recursive', 'patch-canonical', 'tree-shaking').
        is_file (bool): True if input_source is a file path.
        is_url (bool): True if input_source is a URL.
        resolve_dependencies (bool): Whether to resolve and import dependencies.
    
    Returns:
        dict: Import results with 'requested', 'downloaded', 'dependencies', and 'errors'.
    """
    logger.info(f"Starting manual import for {input_source} (mode={dependency_mode}, resolve_deps={resolve_dependencies})")
    download_dir = _get_download_dir()
    if not download_dir:
        return {
            "requested": input_source,
            "downloaded": {},
            "dependencies": [],
            "errors": ["Failed to get download directory."]
        }

    results = {
        "requested": input_source,
        "downloaded": {},
        "dependencies": [],
        "errors": []
    }

    try:
        if is_file:
            tgz_path = input_source
            if not os.path.exists(tgz_path):
                results['errors'].append(f"File not found: {tgz_path}")
                return results
            name, version = parse_package_filename(os.path.basename(tgz_path))
            if not name:
                name = os.path.splitext(os.path.basename(tgz_path))[0]
                version = "unknown"
            target_filename = construct_tgz_filename(name, version)
            target_path = os.path.join(download_dir, target_filename)
            shutil.copy(tgz_path, target_path)
            results['downloaded'][name, version] = target_path
        elif is_url:
            tgz_path = download_manual_package_from_url(input_source, download_dir)
            if not tgz_path:
                results['errors'].append(f"Failed to download package from URL: {input_source}")
                return results
            name, version = parse_package_filename(os.path.basename(tgz_path))
            if not name:
                name = os.path.splitext(os.path.basename(tgz_path))[0]
                version = "unknown"
            results['downloaded'][name, version] = tgz_path
        else:
            tgz_path = download_manual_package(input_source, version, download_dir)
            if not tgz_path:
                results['errors'].append(f"Failed to download {input_source}#{version}")
                return results
            results['downloaded'][input_source, version] = tgz_path
            name = input_source

        if resolve_dependencies:
            pkg_info = process_manual_package_file(tgz_path)
            if pkg_info.get('errors'):
                results['errors'].extend(pkg_info['errors'])
            dependencies = pkg_info.get('dependencies', [])
            results['dependencies'] = dependencies

            if dependencies and dependency_mode != 'tree-shaking':
                for dep in dependencies:
                    dep_name = dep.get('name')
                    dep_version = dep.get('version', 'latest')
                    if not dep_name:
                        continue
                    logger.info(f"Processing dependency {dep_name}#{dep_version}")
                    dep_result = import_manual_package_and_dependencies(
                        dep_name,
                        dep_version,
                        dependency_mode=dependency_mode,
                        resolve_dependencies=True
                    )
                    results['downloaded'].update(dep_result['downloaded'])
                    results['dependencies'].extend(dep_result['dependencies'])
                    results['errors'].extend(dep_result['errors'])

        save_package_metadata(name, version, dependency_mode, results['dependencies'])
        return results
    except Exception as e:
        logger.error(f"Error during manual import of {input_source}: {str(e)}", exc_info=True)
        results['errors'].append(f"Unexpected error: {str(e)}")
        return results

def download_manual_package(package_name, version, download_dir):
    """
    Download a FHIR package from the registry, cloning download_package.
    
    Args:
        package_name (str): Package name.
        version (str): Package version.
        download_dir (str): Directory to save the package.
    
    Returns:
        str: Path to the downloaded file, or None if failed.
    """
    logger.info(f"Attempting manual download of {package_name}#{version}")
    tgz_filename = construct_tgz_filename(package_name, version)
    if not tgz_filename:
        logger.error(f"Invalid filename constructed for {package_name}#{version}")
        return None
    target_path = os.path.join(download_dir, tgz_filename)
    if os.path.exists(target_path):
        logger.info(f"Manual package {package_name}#{version} already exists at {target_path}")
        return target_path

    url = f"{FHIR_REGISTRY_BASE_URL}/{package_name}/{version}"
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(target_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Manually downloaded {package_name}#{version} to {target_path}")
        return target_path
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error downloading {package_name}#{version}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error downloading {package_name}#{version}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading {package_name}#{version}: {e}", exc_info=True)
        return None

def download_manual_package_from_url(url, download_dir):
    """
    Download a FHIR package from a URL, cloning download_package logic.
    
    Args:
        url (str): URL to the .tgz file.
        download_dir (str): Directory to save the package.
    
    Returns:
        str: Path to the downloaded file, or None if failed.
    """
    logger.info(f"Attempting manual download from URL: {url}")
    parsed_url = urlparse(url)
    filename = os.path.basename(parsed_url.path)
    if not filename.endswith('.tgz'):
        logger.error(f"URL does not point to a .tgz file: {filename}")
        return None
    target_path = os.path.join(download_dir, filename)
    if os.path.exists(target_path):
        logger.info(f"Package from {url} already exists at {target_path}")
        return target_path

    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(target_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Manually downloaded package from {url} to {target_path}")
        return target_path
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error downloading from {url}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error downloading from {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading from {url}: {e}", exc_info=True)
        return None

def process_manual_package_file(tgz_path):
    """
    Process a .tgz package file to extract metadata, cloning process_package_file.
    
    Args:
        tgz_path (str): Path to the .tgz file.
    
    Returns:
        dict: Package metadata including dependencies and errors.
    """
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"Package file not found for manual processing: {tgz_path}")
        return {'errors': [f"Package file not found: {tgz_path}"], 'dependencies': []}

    pkg_basename = os.path.basename(tgz_path)
    name, version = parse_package_filename(tgz_path)
    logger.info(f"Manually processing package: {pkg_basename} ({name}#{version})")

    results = {
        'dependencies': [],
        'errors': []
    }

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            pkg_json_member = next((m for m in tar if m.name == 'package/package.json'), None)
            if pkg_json_member:
                with tar.extractfile(pkg_json_member) as f:
                    pkg_data = json.load(f)
                    dependencies = pkg_data.get('dependencies', {})
                    results['dependencies'] = [
                        {'name': dep_name, 'version': dep_version}
                        for dep_name, dep_version in dependencies.items()
                    ]
            else:
                results['errors'].append("package.json not found in archive")
    except Exception as e:
        logger.error(f"Error manually processing {tgz_path}: {e}", exc_info=True)
        results['errors'].append(f"Error processing package: {str(e)}")

    return results

def fetch_packages_from_registries(search_term=''):
    logger.debug("Entering fetch_packages_from_registries function with search_term: %s", search_term)
    packages_dict = defaultdict(list)
    
    try:
        logger.debug("Calling get_additional_registries")
        feed_registries = get_additional_registries()
        logger.debug("Returned from get_additional_registries with %d registries: %s", len(feed_registries), feed_registries)
        
        if not feed_registries:
            logger.warning("No feed registries available. Cannot fetch packages.")
            return []
        
        logger.info(f"Processing {len(feed_registries)} feed registries")
        for feed in feed_registries:
            try:
                logger.info(f"Fetching feed: {feed['name']} from {feed['url']}")
                response = requests.get(feed['url'], timeout=30)
                response.raise_for_status()
                
                # Log the raw response content for debugging
                response_text = response.text[:500]  # Limit to first 500 chars for logging
                logger.debug(f"Raw response from {feed['url']}: {response_text}")
                
                try:
                    data = json.loads(response.text)
                    num_feed_packages = len(data.get('packages', []))
                    logger.info(f"Fetched from feed {feed['name']}: {num_feed_packages} packages (JSON)")
                    for pkg in data.get('packages', []):
                        if not isinstance(pkg, dict):
                            continue
                        pkg_name = pkg.get('name', '')
                        if not pkg_name:
                            continue
                        packages_dict[pkg_name].append(pkg)
                except json.JSONDecodeError:
                    feed_data = feedparser.parse(response.text)
                    if not feed_data.entries:
                        logger.warning(f"No entries found in feed {feed['name']}")
                        continue
                    num_rss_packages = len(feed_data.entries)
                    logger.info(f"Fetched from feed {feed['name']}: {num_rss_packages} packages (Atom/RSS)")
                    logger.info(f"Sample feed entries from {feed['name']}: {feed_data.entries[:2]}")
                    for entry in feed_data.entries:
                        try:
                            # Extract package name and version from title (e.g., "hl7.fhir.au.ereq#0.3.0-preview")
                            title = entry.get('title', '')
                            if '#' in title:
                                pkg_name, version = title.split('#', 1)
                            else:
                                pkg_name = title
                                version = entry.get('version', '')
                            if not pkg_name:
                                pkg_name = entry.get('id', '') or entry.get('summary', '')
                            if not pkg_name:
                                continue
                            
                            package = {
                                'name': pkg_name,
                                'version': version,
                                'author': entry.get('author', ''),
                                'fhirVersion': entry.get('fhir_version', [''])[0] or '',
                                'url': entry.get('link', ''),
                                'canonical': entry.get('canonical', ''),
                                'dependencies': entry.get('dependencies', []),
                                'pubDate': entry.get('published', entry.get('pubdate', '')),
                                'registry': feed['url']
                            }
                            if search_term and package['name'] and search_term.lower() not in package['name'].lower():
                                continue
                            packages_dict[pkg_name].append(package)
                        except Exception as entry_error:
                            logger.error(f"Error processing entry in feed {feed['name']}: {entry_error}")
                            logger.info(f"Problematic entry: {entry}")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    logger.warning(f"Feed endpoint not found for {feed['name']}: {feed['url']} - 404 Not Found")
                else:
                    logger.error(f"HTTP error fetching from feed {feed['name']}: {e}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error fetching from feed {feed['name']}: {e}")
            except Exception as error:
                logger.error(f"Unexpected error fetching from feed {feed['name']}: {error}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_packages_from_registries: {e}")
    
    # Convert packages_dict to a list of packages with aggregated versions
    packages = []
    for pkg_name, entries in packages_dict.items():
        # Aggregate versions with their publication dates
        versions = [
            {
                "version": entry.get('version', ''),
                "pubDate": entry.get('pubDate', '')
            }
            for entry in entries
            if entry.get('version', '')
        ]
        # Sort versions by pubDate (newest first)
        versions.sort(key=lambda x: x.get('pubDate', ''), reverse=True)
        if not versions:
            continue
        
        # Take the latest entry for the main package fields
        latest_entry = entries[0]
        package = {
            'name': pkg_name,
            'version': latest_entry.get('version', ''),
            'latestVersion': latest_entry.get('version', ''),
            'author': latest_entry.get('author', ''),
            'fhirVersion': latest_entry.get('fhirVersion', ''),
            'url': latest_entry.get('url', ''),
            'canonical': latest_entry.get('canonical', ''),
            'dependencies': latest_entry.get('dependencies', []),
            'versions': versions,  # List of versions with pubDate
            'registry': latest_entry.get('registry', '')
        }
        packages.append(package)
    
    logger.info(f"Total packages fetched: {len(packages)}")
    return packages

def normalize_package_data(raw_packages):
    """
    Normalizes package data, identifying latest absolute and latest official versions.
    Uses safe_parse_version for robust comparison.
    """
    packages_grouped = defaultdict(list)
    skipped_raw_count = 0
    for entry in raw_packages:
        if not isinstance(entry, dict):
            skipped_raw_count += 1
            logger.warning(f"Skipping raw package entry, not a dict: {entry}")
            continue
        raw_name = entry.get('name') or entry.get('title') or ''
        if not isinstance(raw_name, str):
            raw_name = str(raw_name)
        name_part = raw_name.split('#', 1)[0].strip().lower()
        if name_part:
            packages_grouped[name_part].append(entry)
        else:
            if not entry.get('id'):
                skipped_raw_count += 1
                logger.warning(f"Skipping raw package entry, no name or id: {entry}")
    logger.info(f"Initial grouping: {len(packages_grouped)} unique package names found. Skipped {skipped_raw_count} raw entries.")

    normalized_list = []
    skipped_norm_count = 0
    total_entries_considered = 0

    for name_key, entries in packages_grouped.items():
        total_entries_considered += len(entries)
        latest_absolute_data = None
        latest_official_data = None
        latest_absolute_ver_for_comp = safe_parse_version("0.0.0a0")
        latest_official_ver_for_comp = safe_parse_version("0.0.0a0")
        all_versions = []
        package_name_display = name_key

        # Aggregate all versions from entries
        processed_versions = set()
        for package_entry in entries:
            versions_list = package_entry.get('versions', [])
            for version_info in versions_list:
                if isinstance(version_info, dict) and 'version' in version_info:
                    version_str = version_info.get('version', '')
                    if version_str and version_str not in processed_versions:
                        all_versions.append(version_info)
                        processed_versions.add(version_str)

        processed_entries = []
        for package_entry in entries:
            version_str = None
            raw_name_entry = package_entry.get('name') or package_entry.get('title') or ''
            if not isinstance(raw_name_entry, str):
                raw_name_entry = str(raw_name_entry)
            version_keys = ['version', 'latestVersion']
            for key in version_keys:
                val = package_entry.get(key)
                if isinstance(val, str) and val:
                    version_str = val.strip()
                    break
                elif isinstance(val, list) and val and isinstance(val[0], str) and val[0]:
                    version_str = val[0].strip()
                    break
            if not version_str and '#' in raw_name_entry:
                parts = raw_name_entry.split('#', 1)
                if len(parts) == 2 and parts[1]:
                    version_str = parts[1].strip()

            if not version_str:
                logger.warning(f"Skipping entry for {raw_name_entry}: no valid version found. Entry: {package_entry}")
                skipped_norm_count += 1
                continue

            version_str = version_str.strip()
            current_display_name = str(raw_name_entry).split('#')[0].strip()
            if current_display_name and current_display_name != name_key:
                package_name_display = current_display_name

            entry_with_version = package_entry.copy()
            entry_with_version['version'] = version_str
            processed_entries.append(entry_with_version)

            try:
                current_ver_obj_for_comp = safe_parse_version(version_str)
                if latest_absolute_data is None or current_ver_obj_for_comp > latest_absolute_ver_for_comp:
                    latest_absolute_ver_for_comp = current_ver_obj_for_comp
                    latest_absolute_data = entry_with_version

                if re.match(r'^\d+\.\d+\.\d+(?:-[a-zA-Z0-9\.]+)?$', version_str):
                    if latest_official_data is None or current_ver_obj_for_comp > latest_official_ver_for_comp:
                        latest_official_ver_for_comp = current_ver_obj_for_comp
                        latest_official_data = entry_with_version
            except Exception as comp_err:
                logger.error(f"Error comparing version '{version_str}' for package '{package_name_display}': {comp_err}", exc_info=True)

        if latest_absolute_data:
            final_absolute_version = latest_absolute_data.get('version', 'unknown')
            final_official_version = latest_official_data.get('version') if latest_official_data else None

            author_raw = latest_absolute_data.get('author') or latest_absolute_data.get('publisher') or ''
            if isinstance(author_raw, dict):
                author = author_raw.get('name', str(author_raw))
            elif not isinstance(author_raw, str):
                author = str(author_raw)
            else:
                author = author_raw

            fhir_version_str = None
            fhir_keys = ['fhirVersion', 'fhirVersions', 'fhir_version']
            for key in fhir_keys:
                val = latest_absolute_data.get(key)
                if isinstance(val, list) and val and isinstance(val[0], str):
                    fhir_version_str = val[0]
                    break
                elif isinstance(val, str) and val:
                    fhir_version_str = val
                    break
            fhir_version_str = fhir_version_str or 'unknown'

            url_raw = latest_absolute_data.get('url') or latest_absolute_data.get('link') or ''
            url = str(url_raw) if not isinstance(url_raw, str) else url_raw
            canonical_raw = latest_absolute_data.get('canonical') or url
            canonical = str(canonical_raw) if not isinstance(canonical_raw, str) else canonical_raw

            dependencies_raw = latest_absolute_data.get('dependencies', [])
            dependencies = []
            if isinstance(dependencies_raw, dict):
                dependencies = [{"name": str(dn), "version": str(dv)} for dn, dv in dependencies_raw.items()]
            elif isinstance(dependencies_raw, list):
                for dep in dependencies_raw:
                    if isinstance(dep, str):
                        if '@' in dep:
                            dep_name, dep_version = dep.split('@', 1)
                            dependencies.append({"name": dep_name, "version": dep_version})
                        else:
                            dependencies.append({"name": dep, "version": "N/A"})
                    elif isinstance(dep, dict) and 'name' in dep and 'version' in dep:
                        dependencies.append(dep)

            # Sort all_versions by pubDate (newest first)
            all_versions.sort(key=lambda x: x.get('pubDate', ''), reverse=True)

            normalized_entry = {
                'name': package_name_display,
                'version': final_absolute_version,
                'latest_absolute_version': final_absolute_version,
                'latest_official_version': final_official_version,
                'author': author.strip(),
                'fhir_version': fhir_version_str.strip(),
                'url': url.strip(),
                'canonical': canonical.strip(),
                'dependencies': dependencies,
                'version_count': len(all_versions),
                'all_versions': all_versions,  # Preserve the full versions list with pubDate
                'versions_data': processed_entries,
                'registry': latest_absolute_data.get('registry', '')
            }
            normalized_list.append(normalized_entry)
            if not final_official_version:
                logger.warning(f"No official version found for package '{package_name_display}'. Versions: {[v['version'] for v in all_versions]}")
        else:
            logger.warning(f"No valid entries found to determine details for package name key '{name_key}'. Entries: {entries}")
            skipped_norm_count += len(entries)

    logger.info(f"Normalization complete. Entries considered: {total_entries_considered}, Skipped during norm: {skipped_norm_count}, Unique Packages Found: {len(normalized_list)}")
    normalized_list.sort(key=lambda x: x.get('name', '').lower())
    return normalized_list

def cache_packages(normalized_packages, db, CachedPackage):
    """
    Cache normalized FHIR Implementation Guide packages in the CachedPackage database.
    Updates existing records or adds new ones to improve performance for other routes.
    
    Args:
        normalized_packages (list): List of normalized package dictionaries.
        db: The SQLAlchemy database instance.
        CachedPackage: The CachedPackage model class.
    """
    try:
        for package in normalized_packages:
            existing = CachedPackage.query.filter_by(package_name=package['name'], version=package['version']).first()
            if existing:
                existing.author = package['author']
                existing.fhir_version = package['fhir_version']
                existing.version_count = package['version_count']
                existing.url = package['url']
                existing.all_versions = package['all_versions']
                existing.dependencies = package['dependencies']
                existing.latest_absolute_version = package['latest_absolute_version']
                existing.latest_official_version = package['latest_official_version']
                existing.canonical = package['canonical']
                existing.registry = package.get('registry', '')
            else:
                new_package = CachedPackage(
                    package_name=package['name'],
                    version=package['version'],
                    author=package['author'],
                    fhir_version=package['fhir_version'],
                    version_count=package['version_count'],
                    url=package['url'],
                    all_versions=package['all_versions'],
                    dependencies=package['dependencies'],
                    latest_absolute_version=package['latest_absolute_version'],
                    latest_official_version=package['latest_official_version'],
                    canonical=package['canonical'],
                    registry=package.get('registry', '')
                )
                db.session.add(new_package)
        db.session.commit()
        logger.info(f"Cached {len(normalized_packages)} packages in CachedPackage.")
    except Exception as error:
        db.session.rollback()
        logger.error(f"Error caching packages: {error}")
        raise

#-----------------------------------------------------------------------

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

# --- Helper to get description (Add this to services.py) ---
def get_package_description(package_name, package_version, packages_dir):
    """Reads package.json from a tgz and returns the description."""
    tgz_filename = construct_tgz_filename(package_name, package_version)
    if not tgz_filename: return "Error: Could not construct filename."
    tgz_path = os.path.join(packages_dir, tgz_filename)
    if not os.path.exists(tgz_path):
        return f"Error: Package file not found ({tgz_filename})."

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            pkg_json_member = next((m for m in tar if m.name == 'package/package.json'), None)
            if pkg_json_member:
                with tar.extractfile(pkg_json_member) as f:
                    pkg_data = json.load(f)
                    return pkg_data.get('description', 'No description found in package.json.')
            else:
                return "Error: package.json not found in archive."
    except (tarfile.TarError, json.JSONDecodeError, KeyError, IOError, Exception) as e:
        logger.error(f"Error reading description from {tgz_filename}: {e}")
        return f"Error reading package details: {e}"

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

# --- Helper Function to Find References (Keep as before) ---
def find_references(element, refs_list):
    """
    Recursively finds all 'reference' strings within a FHIR resource element (dict or list).
    Appends found reference strings to refs_list.
    """
    if isinstance(element, dict):
        for key, value in element.items():
            if key == 'reference' and isinstance(value, str):
                refs_list.append(value)
            elif isinstance(value, (dict, list)):
                find_references(value, refs_list) # Recurse
    elif isinstance(element, list):
        for item in element:
            if isinstance(item, (dict, list)):
                find_references(item, refs_list) # Recurse

# --- NEW: Helper Function for Basic FHIR XML to Dict ---
def basic_fhir_xml_to_dict(xml_string):
    """
    Very basic conversion of FHIR XML to a dictionary.
    Focuses on resourceType, id, and finding reference elements/attributes.
    NOTE: This is NOT a complete or robust FHIR XML parser. Use with caution.
    Returns a dictionary representation or None if parsing fails.
    """
    try:
        # Replace namespace prefixes for easier parsing with ElementTree find methods
        # This is a common simplification but might fail for complex XML namespaces
        xml_string_no_ns = re.sub(r' xmlns="[^"]+"', '', xml_string, count=1)
        xml_string_no_ns = re.sub(r' xmlns:[^=]+="[^"]+"', '', xml_string_no_ns)
        root = ET.fromstring(xml_string_no_ns)

        resource_dict = {"resourceType": root.tag}

        # Find 'id' element usually directly under the root
        id_element = root.find("./id[@value]")
        if id_element is not None:
            resource_dict["id"] = id_element.get("value")
        else: # Check if id is an attribute of the root (less common)
             res_id = root.get("id")
             if res_id: resource_dict["id"] = res_id

        # Recursively find 'reference' elements and extract their 'value' attribute
        references = []
        for ref_element in root.findall(".//reference[@value]"):
            ref_value = ref_element.get("value")
            if ref_value:
                references.append({"reference": ref_value}) # Store in a way find_references can find

        # Find other potential references (e.g., url attributes on certain elements)
        # This needs to be expanded based on common FHIR patterns if needed
        for url_element in root.findall(".//*[@url]"): # Find any element with a 'url' attribute
             url_value = url_element.get("url")
             # Basic check if it looks like a resource reference (simplistic)
             if url_value and ('/' in url_value or url_value.startswith('urn:')):
                  # Decide how to store this - maybe add to a specific key?
                  # For now, let's add it to a generic '_references_from_url' list
                  if '_references_from_url' not in resource_dict:
                      resource_dict['_references_from_url'] = []
                  resource_dict['_references_from_url'].append({"reference": url_value})


        # Add references found into the main dict structure so find_references can process them
        if references or '_references_from_url' in resource_dict:
             # Combine them - choose a suitable key, e.g., '_extracted_references'
             resource_dict['_extracted_references'] = references + resource_dict.get('_references_from_url', [])

        # Include raw XML for debugging or potential later use
        # resource_dict["_xml_content"] = xml_string
        return resource_dict

    except ET.ParseError as e:
        logger.error(f"XML Parse Error during basic conversion: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during basic_fhir_xml_to_dict: {e}", exc_info=True)
        return None

# def parse_package_filename(filename):
#     """Parses a standard FHIR package filename into name and version."""
#     if not filename or not filename.endswith('.tgz'):
#         logger.debug(f"Filename '{filename}' does not end with .tgz")
#         return None, None
#     base_name = filename[:-4]
#     last_hyphen_index = base_name.rfind('-')
#     while last_hyphen_index != -1:
#         potential_name = base_name[:last_hyphen_index]
#         potential_version = base_name[last_hyphen_index + 1:]
#         if potential_version and (potential_version[0].isdigit() or any(potential_version.startswith(kw) for kw in ['v', 'dev', 'draft', 'preview', 'release', 'alpha', 'beta'])):
#             name = potential_name.replace('_', '.')
#             version = potential_version
#             logger.debug(f"Parsed '{filename}' -> name='{name}', version='{version}'")
#             return name, version
#         else:
#             last_hyphen_index = base_name.rfind('-', 0, last_hyphen_index)
#     logger.warning(f"Could not parse version from '{filename}'. Treating '{base_name}' as name.")
#     name = base_name.replace('_', '.')
#     version = ""
#     return name, version

def parse_package_filename(filename):
    """
    Parses a standard FHIR package filename into name and version.
    Handles various version formats including semantic versions, pre-releases, snapshots, and complex suffixes.
    """
    if not filename or not filename.endswith('.tgz'):
        logger.debug(f"Filename '{filename}' does not end with .tgz")
        return None, None
    
    base_name = filename[:-4]  # Remove '.tgz'

    # Define a comprehensive pattern for FHIR package versions as a single string
    # Matches versions like:
    # - 1.0.0, 4.0.2
    # - 1.1.0-preview, 0.1.0-draft, 1.0.0-ballot-3
    # - 1.0.0-alpha.1, 1.0.0-RC2, 0.9.0-alpha1.0.8
    # - 1.1.0-snapshot-3, 0.0.1-snapshot
    # - 2.3.5-buildnumbersuffix2
    version_pattern = r'(\d+\.\d+\.\d+)(?:-(?:preview|ballot|draft|snapshot|alpha|beta|RC\d*|buildnumbersuffix\d*|alpha\d+\.\d+\.\d+|snapshot-\d+|ballot-\d+|alpha\.\d+))?$'

    # Find the last occurrence of the version pattern in the base_name
    match = None
    for i in range(len(base_name), 0, -1):
        substring = base_name[:i]
        if re.search(version_pattern, substring):
            match = re.search(version_pattern, base_name[:i])
            if match:
                break
    
    if not match:
        logger.warning(f"Could not parse version from '{filename}'. Treating '{base_name}' as name.")
        name = base_name.replace('_', '.')
        version = ""
        return name, version

    # Extract the matched version
    version_start_idx = match.start(1)  # Start of the version (e.g., start of "1.1.0" in "1.1.0-preview")
    name = base_name[:version_start_idx].rstrip('-').replace('_', '.')  # Everything before the version
    version = base_name[version_start_idx:]  # The full version string

    # Validate the name and version
    if not name or not version:
        logger.warning(f"Invalid parse for '{filename}': name='{name}', version='{version}'. Using fallback.")
        name = base_name.replace('_', '.')
        version = ""
        return name, version

    logger.debug(f"Parsed '{filename}' -> name='{name}', version='{version}'")
    return name, version

def remove_narrative(resource, include_narrative=False):
    """Remove narrative text element from a FHIR resource if not including narrative."""
    if isinstance(resource, dict) and not include_narrative:
        if 'text' in resource:
            logger.debug(f"Removing narrative text from resource: {resource.get('resourceType', 'unknown')}")
            del resource['text']
        if resource.get('resourceType') == 'Bundle' and 'entry' in resource:
            resource['entry'] = [
                dict(entry, resource=remove_narrative(entry.get('resource'), include_narrative))
                if entry.get('resource') else entry
                for entry in resource['entry']
            ]
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

def find_and_extract_sd(tgz_path, resource_identifier, profile_url=None, include_narrative=False, raw=False):
    """Helper to find and extract StructureDefinition json from a tgz path, prioritizing profile match."""
    sd_data = None
    found_path = None
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"File not found in find_and_extract_sd: {tgz_path}")
        return None, None
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            logger.debug(f"Searching for SD matching '{resource_identifier}' with profile '{profile_url}' in {os.path.basename(tgz_path)}")
            potential_matches = []
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
                            sd_filename_base = os.path.splitext(os.path.basename(member.name))[0]
                            sd_filename_lower = sd_filename_base.lower()
                            resource_identifier_lower = resource_identifier.lower() if resource_identifier else None
                            match_score = 0
                            if profile_url and sd_url == profile_url:
                                match_score = 5
                                sd_data = remove_narrative(data, include_narrative)
                                found_path = member.name
                                logger.info(f"Found definitive SD matching profile '{profile_url}' at path: {found_path}")
                                break
                            elif resource_identifier_lower:
                                if sd_id and resource_identifier_lower == sd_id.lower():
                                    match_score = 4
                                elif sd_name and resource_identifier_lower == sd_name.lower():
                                    match_score = 4
                                elif sd_filename_lower == f"structuredefinition-{resource_identifier_lower}":
                                    match_score = 3
                                elif sd_type and resource_identifier_lower == sd_type.lower() and not re.search(r'[-.]', resource_identifier):
                                    match_score = 2
                                elif resource_identifier_lower in sd_filename_lower:
                                    match_score = 1
                                elif sd_url and resource_identifier_lower in sd_url.lower():
                                    match_score = 1
                            if match_score > 0:
                                potential_matches.append((match_score, remove_narrative(data, include_narrative), member.name))
                                if match_score >= 3:
                                    sd_data = remove_narrative(data, include_narrative)
                                    found_path = member.name
                                    break
                except json.JSONDecodeError as e:
                    logger.debug(f"Could not parse JSON in {member.name}, skipping: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode UTF-8 in {member.name}, skipping: {e}")
                except tarfile.TarError as e:
                    logger.warning(f"Tar error reading member {member.name}, skipping: {e}")
                except Exception as e:
                    logger.warning(f"Could not read/parse potential SD {member.name}, skipping: {e}")
                finally:
                    if fileobj:
                        fileobj.close()
            if not sd_data and potential_matches:
                potential_matches.sort(key=lambda x: x[0], reverse=True)
                best_match = potential_matches[0]
                sd_data = best_match[1]
                found_path = best_match[2]
                logger.info(f"Selected best match for '{resource_identifier}' from potential matches (Score: {best_match[0]}): {found_path}")
            if sd_data is None:
                logger.info(f"SD matching identifier '{resource_identifier}' or profile '{profile_url}' not found within archive {os.path.basename(tgz_path)}")
            elif raw:
                # Return the full, unprocessed StructureDefinition JSON
                with tarfile.open(tgz_path, "r:gz") as tar:
                    fileobj = tar.extractfile(found_path)
                    content_bytes = fileobj.read()
                    content_string = content_bytes.decode('utf-8-sig')
                    raw_data = json.loads(content_string)
                    return remove_narrative(raw_data, include_narrative), found_path
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

def process_package_file(tgz_path):
    """
    Extracts types, profile status, MS elements, examples, profile relationships,
    and search parameter conformance from a downloaded .tgz package.
    """
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"Package file not found for processing: {tgz_path}")
        return {'errors': [f"Package file not found: {tgz_path}"], 'resource_types_info': []}

    pkg_basename = os.path.basename(tgz_path)
    name, version = parse_package_filename(tgz_path) # Assumes parse_package_filename exists
    logger.info(f"Processing package file details: {pkg_basename} ({name}#{version})")

    # Initialize results dictionary
    results = {
        'resource_types_info': [],
        'must_support_elements': {},
        'examples': {},
        'complies_with_profiles': [],
        'imposed_profiles': [],
        'search_param_conformance': {}, # Dictionary to store conformance
        'errors': []
    }

    # Intermediate storage for processing
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
    capability_statement_data = None # Store the main CapabilityStatement

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            members = tar.getmembers()
            logger.debug(f"Found {len(members)} members in {pkg_basename}")

            # Filter for relevant JSON files once
            json_members = []
            for m in members:
                if m.isfile() and m.name.startswith('package/') and m.name.lower().endswith('.json'):
                     # Exclude common metadata files by basename
                     basename_lower = os.path.basename(m.name).lower()
                     if basename_lower not in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                         json_members.append(m)
            logger.debug(f"Found {len(json_members)} potential JSON resource members.")

            # --- Pass 1: Process StructureDefinitions and Find CapabilityStatement ---
            logger.debug("Pass 1: Processing StructureDefinitions and finding CapabilityStatement...")
            for member in json_members:
                fileobj = None
                try:
                    fileobj = tar.extractfile(member)
                    if not fileobj: continue

                    content_bytes = fileobj.read()
                    # Handle potential BOM (Byte Order Mark)
                    content_string = content_bytes.decode('utf-8-sig')
                    data = json.loads(content_string)

                    if not isinstance(data, dict): continue
                    resourceType = data.get('resourceType')

                    # --- Process StructureDefinition ---
                    if resourceType == 'StructureDefinition':
                        data = remove_narrative(data) # Assumes remove_narrative exists
                        profile_id = data.get('id') or data.get('name')
                        sd_type = data.get('type')
                        sd_base = data.get('baseDefinition')
                        is_profile_sd = bool(sd_base)

                        if not profile_id or not sd_type:
                            logger.warning(f"Skipping SD {member.name}: missing ID ('{profile_id}') or Type ('{sd_type}').")
                            continue

                        entry_key = profile_id
                        entry = resource_info[entry_key]
                        if entry.get('sd_processed'): continue # Avoid reprocessing

                        logger.debug(f"Processing SD: {entry_key} (type={sd_type}, profile={is_profile_sd})")
                        entry['name'] = entry_key
                        entry['type'] = sd_type
                        entry['is_profile'] = is_profile_sd
                        entry['sd_processed'] = True
                        referenced_types.add(sd_type)

                        # Extract compliesWith/imposed profile URLs
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
                        # Add unique URLs to results
                        results['complies_with_profiles'].extend(c for c in complies_with if c not in results['complies_with_profiles'])
                        results['imposed_profiles'].extend(i for i in imposed if i not in results['imposed_profiles'])

                        # Must Support and Optional Usage Logic
                        has_ms_in_this_sd = False
                        ms_paths_in_this_sd = set()
                        elements = data.get('snapshot', {}).get('element', []) or data.get('differential', {}).get('element', [])
                        for element in elements:
                             if not isinstance(element, dict): continue
                             must_support = element.get('mustSupport')
                             element_id = element.get('id')
                             element_path = element.get('path')
                             slice_name = element.get('sliceName')
                             if must_support is True:
                                 if element_id and element_path:
                                     # Use element ID as the key for MS paths unless it's a slice
                                     ms_path_key = f"{element_path}[sliceName='{slice_name}']" if slice_name else element_id
                                     ms_paths_in_this_sd.add(ms_path_key)
                                     has_ms_in_this_sd = True
                                 else:
                                     logger.warning(f"MS=true without path/id in {entry_key} ({member.name})")
                                     has_ms_in_this_sd = True

                        if has_ms_in_this_sd:
                            entry['ms_paths'].update(ms_paths_in_this_sd)
                            entry['ms_flag'] = True

                        if sd_type == 'Extension' and has_ms_in_this_sd:
                             # Check if any MustSupport path is internal to the Extension definition
                             internal_ms_exists = any(p.startswith('Extension.') or ':' in p for p in entry['ms_paths'])
                             if internal_ms_exists:
                                 entry['optional_usage'] = True
                                 logger.info(f"Marked Extension {entry_key} as optional_usage")

                    # --- Find CapabilityStatement ---
                    elif resourceType == 'CapabilityStatement':
                        # Store the first one found. Add logic here if specific selection needed.
                        if capability_statement_data is None:
                            capability_statement_data = data
                            logger.info(f"Found primary CapabilityStatement in: {member.name} (ID: {data.get('id', 'N/A')})")
                        else:
                             logger.warning(f"Found multiple CapabilityStatements. Using first found ({capability_statement_data.get('id', 'unknown')}). Ignoring {member.name}.")

                # Error handling for individual file processing
                except json.JSONDecodeError as e: logger.warning(f"JSON parse error in {member.name}: {e}"); results['errors'].append(f"JSON error in {member.name}")
                except UnicodeDecodeError as e: logger.warning(f"Encoding error in {member.name}: {e}"); results['errors'].append(f"Encoding error in {member.name}")
                except Exception as e: logger.warning(f"Error processing member {member.name}: {e}", exc_info=False); results['errors'].append(f"Processing error in {member.name}: {e}")
                finally:
                    if fileobj: fileobj.close()
            # --- End Pass 1 ---

            # --- Pass 1.5: Process CapabilityStatement for Search Param Conformance ---
            if capability_statement_data:
                logger.debug("Processing CapabilityStatement for Search Parameter Conformance...")
                conformance_map = defaultdict(dict)
                # Standard FHIR extension URL for defining expectations
                expectation_extension_url = "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation"

                for rest_component in capability_statement_data.get('rest', []):
                    for resource_component in rest_component.get('resource', []):
                        resource_type = resource_component.get('type')
                        if not resource_type: continue

                        for search_param in resource_component.get('searchParam', []):
                            param_name = search_param.get('name')
                            param_doc = search_param.get('documentation', '')
                            # Default conformance level if not explicitly stated
                            conformance_level = 'Optional'

                            # Check for the standard expectation extension first
                            extensions = search_param.get('extension', [])
                            expectation_ext = next((ext for ext in extensions if ext.get('url') == expectation_extension_url), None)

                            if expectation_ext and expectation_ext.get('valueCode'):
                                # Use the value from the standard extension
                                conformance_code = expectation_ext['valueCode'].upper()
                                # Map to SHALL, SHOULD, MAY - adjust if other codes are used by the IG
                                if conformance_code in ['SHALL', 'SHOULD', 'MAY', 'SHOULD-NOT']: # Add more if needed
                                     conformance_level = conformance_code
                                else:
                                     logger.warning(f"Unknown expectation code '{expectation_ext['valueCode']}' for {resource_type}.{param_name}. Defaulting to Optional.")
                                logger.debug(f"  Conformance for {resource_type}.{param_name} from extension: {conformance_level}")
                            elif param_doc:
                                # Fallback: Check documentation string for keywords (less reliable)
                                doc_lower = param_doc.lower()
                                if 'shall' in doc_lower: conformance_level = 'SHALL'
                                elif 'should' in doc_lower: conformance_level = 'SHOULD'
                                elif 'may' in doc_lower: conformance_level = 'MAY'
                                if conformance_level != 'Optional':
                                     logger.debug(f"  Conformance for {resource_type}.{param_name} from documentation keywords: {conformance_level}")

                            if param_name:
                                conformance_map[resource_type][param_name] = conformance_level

                results['search_param_conformance'] = dict(conformance_map) # Convert back to regular dict
                logger.info(f"Extracted Search Parameter conformance rules for {len(conformance_map)} resource types.")
                # logger.debug(f"Full Conformance Map: {json.dumps(results['search_param_conformance'], indent=2)}") # Optional detailed logging
            else:
                 logger.warning(f"No CapabilityStatement found in package {pkg_basename}. Search parameter conformance data will be unavailable.")
            # --- End Pass 1.5 ---

            # --- Pass 2: Process Examples ---
            logger.debug("Pass 2: Processing Examples...")
            example_members = [m for m in members if m.isfile() and m.name.startswith('package/') and 'example' in m.name.lower()]

            for member in example_members:
                # Skip metadata files again just in case
                basename_lower = os.path.basename(member.name).lower()
                if basename_lower in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']: continue

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
                        resource_type_ex = data.get('resourceType')
                        if not resource_type_ex: continue

                        # Find association key (profile or type)
                        profile_meta = data.get('meta', {}).get('profile', [])
                        found_profile_match = False
                        if profile_meta and isinstance(profile_meta, list):
                            for profile_url in profile_meta:
                                if profile_url and isinstance(profile_url, str):
                                    # Try matching by ID derived from profile URL first
                                    profile_id_from_meta = profile_url.split('/')[-1]
                                    if profile_id_from_meta in resource_info:
                                        associated_key = profile_id_from_meta
                                        found_profile_match = True
                                        break
                                    # Fallback to matching by full profile URL if needed
                                    elif profile_url in resource_info:
                                        associated_key = profile_url
                                        found_profile_match = True
                                        break
                        # If no profile match, associate with base resource type
                        if not found_profile_match:
                            key_to_use = resource_type_ex
                            # Ensure the base type exists in resource_info
                            if key_to_use not in resource_info:
                                resource_info[key_to_use].update({'name': key_to_use, 'type': resource_type_ex, 'is_profile': False})
                            associated_key = key_to_use

                        referenced_types.add(resource_type_ex) # Track type even if example has profile

                    else: # Guessing for non-JSON examples
                         guessed_type = basename_lower.split('-')[0].capitalize()
                         guessed_profile_id = basename_lower.split('-')[0] # Often filename starts with profile ID
                         key_to_use = None
                         if guessed_profile_id in resource_info: key_to_use = guessed_profile_id
                         elif guessed_type in resource_info: key_to_use = guessed_type
                         else: # Add base type if not seen
                              key_to_use = guessed_type
                              resource_info[key_to_use].update({'name': key_to_use, 'type': key_to_use, 'is_profile': False})
                         associated_key = key_to_use
                         referenced_types.add(guessed_type)

                    # Add example filename to the associated resource/profile
                    if associated_key:
                        resource_info[associated_key]['examples'].add(member.name)
                        # logger.debug(f"Associated example {member.name} with {associated_key}")
                    else:
                        logger.warning(f"Could not associate example {member.name} with any known resource or profile.")

                # --- CORRECTED INDENTATION FOR FINALLY BLOCK ---
                except json.JSONDecodeError as e: logger.warning(f"Could not parse JSON example {member.name}: {e}")
                except UnicodeDecodeError as e: logger.warning(f"Could not decode example {member.name}: {e}")
                except tarfile.TarError as e: logger.warning(f"TarError reading example {member.name}: {e}")
                except Exception as e: logger.warning(f"Could not process example member {member.name}: {e}", exc_info=False)
                finally:
                     if fileobj: fileobj.close()
            # --- End Pass 2 ---

            # --- Pass 3: Ensure Relevant Base Types ---
            logger.debug("Pass 3: Ensuring relevant base types...")
            essential_types = {'CapabilityStatement'} # Add any other types vital for display/logic
            for type_name in referenced_types | essential_types:
                # Check against a predefined list of valid FHIR types (FHIR_R4_BASE_TYPES)
                if type_name in FHIR_R4_BASE_TYPES and type_name not in resource_info:
                    resource_info[type_name]['name'] = type_name
                    resource_info[type_name]['type'] = type_name
                    resource_info[type_name]['is_profile'] = False
                    logger.debug(f"Added base type entry for {type_name}")
            # --- End Pass 3 ---

            # --- Final Consolidation ---
            logger.debug(f"Finalizing results from {len(resource_info)} resource_info entries...")
            final_list = []
            final_ms_elements = {}
            final_examples = {}
            for key, info in resource_info.items():
                display_name = info.get('name') or key
                base_type = info.get('type')
                # Skip entries missing essential info (should be rare now)
                if not display_name or not base_type:
                     logger.warning(f"Skipping final format for incomplete key: {key} - Info: {info}")
                     continue
                # Add to final list for UI display
                final_list.append({
                    'name': display_name,
                    'type': base_type,
                    'is_profile': info.get('is_profile', False),
                    'must_support': info.get('ms_flag', False),
                    'optional_usage': info.get('optional_usage', False)
                })
                # Add Must Support paths if present
                if info['ms_paths']:
                     final_ms_elements[display_name] = sorted(list(info['ms_paths']))
                # Add Examples if present
                if info['examples']:
                     final_examples[display_name] = sorted(list(info['examples']))

            # Store final lists/dicts in results
            results['resource_types_info'] = sorted(final_list, key=lambda x: (not x.get('is_profile', False), x.get('name', '')))
            results['must_support_elements'] = final_ms_elements
            results['examples'] = final_examples
            logger.debug(f"Final must_support_elements count: {len(final_ms_elements)}")
            logger.debug(f"Final examples count: {len(final_examples)}")
            # --- End Final Consolidation ---

    # Exception handling for opening/reading the tarfile itself
    except tarfile.ReadError as e: err_msg = f"Tar ReadError processing package file {pkg_basename}: {e}"; logger.error(err_msg); results['errors'].append(err_msg)
    except tarfile.TarError as e: err_msg = f"TarError processing package file {pkg_basename}: {e}"; logger.error(err_msg); results['errors'].append(err_msg)
    except FileNotFoundError: err_msg = f"Package file not found during processing: {tgz_path}"; logger.error(err_msg); results['errors'].append(err_msg)
    except Exception as e: err_msg = f"Unexpected error processing package file {pkg_basename}: {e}"; logger.error(err_msg, exc_info=True); results['errors'].append(err_msg)

    # --- Final Summary Logging ---
    final_types_count = len(results['resource_types_info'])
    ms_count = sum(1 for r in results['resource_types_info'] if r.get('must_support'))
    optional_ms_count = sum(1 for r in results['resource_types_info'] if r.get('optional_usage'))
    total_ms_paths = sum(len(v) for v in results['must_support_elements'].values())
    total_examples = sum(len(v) for v in results['examples'].values())
    total_conf_types = len(results['search_param_conformance'])
    total_conf_params = sum(len(v) for v in results['search_param_conformance'].values())

    logger.info(f"Package processing finished for {pkg_basename}: "
                f"{final_types_count} Res/Profs; {ms_count} MS ({optional_ms_count} OptExt); {total_ms_paths} MS paths; "
                f"{total_examples} Exs; Comp={len(results['complies_with_profiles'])}; Imp={len(results['imposed_profiles'])}; "
                f"ConfParams={total_conf_params} for {total_conf_types} types; Errors={len(results['errors'])}")

    return results # Return the full results dictionary


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
            hapi_url = f"{current_app.config['HAPI_FHIR_URL'].rstrip('/')}/{resource['resourceType']}/$validate?profile={result['profile']}"
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

# def download_package(name, version):
#     """Downloads a single FHIR package."""
#     download_dir = _get_download_dir()
#     if not download_dir: return None, "Download dir error"
#     filename = construct_tgz_filename(name, version)
#     if not filename: return None, "Filename construction error"
#     save_path = os.path.join(download_dir, filename)
#     if os.path.exists(save_path):
#         logger.info(f"Package already exists: {save_path}")
#         return save_path, None
#     package_url = f"{FHIR_REGISTRY_BASE_URL}/{name}/{version}"
#     try:
#         with requests.get(package_url, stream=True, timeout=60) as r:
#             r.raise_for_status()
#             with open(save_path, 'wb') as f:
#                 for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
#         logger.info(f"Downloaded {filename}")
#         return save_path, None
#     except requests.exceptions.RequestException as e:
#         logger.error(f"Download failed for {name}#{version}: {e}")
#         return None, f"Download error: {e}"
#     except IOError as e:
#         logger.error(f"File write error for {save_path}: {e}")
#         return None, f"File write error: {e}"

def download_package(name, version, dependency_mode='none'):
    """Downloads a FHIR package by name and version to the configured directory."""
    download_dir = _get_download_dir()
    if not download_dir:
        return None, ["Could not determine download directory"]
    tgz_filename = construct_tgz_filename(name, version)
    if not tgz_filename:
        return None, [f"Could not construct filename for {name}#{version}"]
    download_path = os.path.join(download_dir, tgz_filename)
    errors = []

    # Check if already downloaded
    if os.path.exists(download_path):
        logger.info(f"Package {name}#{version} already downloaded at {download_path}")
        return download_path, []

    # Primary download URL
    primary_url = f"{FHIR_REGISTRY_BASE_URL}/{name}/{version}"
    logger.info(f"Attempting download of {name}#{version} from {primary_url}")

    try:
        response = requests.get(primary_url, timeout=30)
        response.raise_for_status()
        with open(download_path, 'wb') as f:
            f.write(response.content)
        logger.info(f"Successfully downloaded {name}#{version} to {download_path}")
        save_package_metadata(name, version, dependency_mode, [])
        return download_path, []
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"Primary download failed (404) for {name}#{version} at {primary_url}. Attempting fallback URL.")
        else:
            error_msg = f"Download error for {name}#{version}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)
            return None, errors
    except requests.exceptions.RequestException as e:
        error_msg = f"Download error for {name}#{version}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return None, errors
    except Exception as e:
        error_msg = f"Unexpected error downloading {name}#{version}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return None, errors

    # Fallback: Try the package's URL from the normalized package data
    if errors and "404" in errors[0]:
        logger.info(f"Looking up alternative download URL for {name}#{version}")
        try:
            # Access the in-memory cache from the Flask app config
            normalized_packages = current_app.config.get('MANUAL_PACKAGE_CACHE', [])
            package_data = next((pkg for pkg in normalized_packages if pkg.get('name') == name), None)
            if not package_data:
                error_msg = f"Package {name} not found in cache for fallback download."
                logger.error(error_msg)
                errors.append(error_msg)
                return None, errors

            package_url = package_data.get('url')
            if not package_url:
                error_msg = f"No alternative URL found for {name}#{version}."
                logger.error(error_msg)
                errors.append(error_msg)
                return None, errors

            # Construct a download URL using the package's URL
            # Assuming the URL is a base (e.g., https://packages.simplifier.net/fhir.ieb.core)
            # and we append the version to form the download URL
            # This may need adjustment based on the actual format of 'url'
            fallback_url = f"{package_url.rstrip('/')}/{version}.tgz"
            logger.info(f"Attempting fallback download of {name}#{version} from {fallback_url}")

            response = requests.get(fallback_url, timeout=30)
            response.raise_for_status()
            with open(download_path, 'wb') as f:
                f.write(response.content)
            logger.info(f"Successfully downloaded {name}#{version} using fallback URL to {download_path}")
            save_package_metadata(name, version, dependency_mode, [])
            return download_path, []
        except requests.exceptions.HTTPError as e:
            error_msg = f"Fallback download error for {name}#{version} at {fallback_url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)
            return None, errors
        except requests.exceptions.RequestException as e:
            error_msg = f"Fallback download network error for {name}#{version}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)
            return None, errors
        except Exception as e:
            error_msg = f"Unexpected error during fallback download of {name}#{version}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)
            return None, errors

    return None, errors

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
    logger.info(f"Starting import of {initial_name}#{initial_version} with mode {dependency_mode}")
    download_dir = _get_download_dir()
    if not download_dir:
        logger.error("Download directory not accessible")
        return {
            'requested': (initial_name, initial_version),
            'processed': set(),
            'downloaded': {},
            'all_dependencies': {},
            'dependencies': [],
            'errors': ['Download directory not accessible']
        }

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
        logger.info(f"Processing package {name}#{version}")
        save_path, dl_error = download_package(name, version)
        if dl_error:
            logger.error(f"Download failed for {name}#{version}: {dl_error}")
            results['errors'].append(f"Download failed for {name}#{version}: {dl_error}")
            continue
        tgz_filename = os.path.basename(save_path)
        logger.info(f"Downloaded {tgz_filename}")
        results['downloaded'][package_id_tuple] = save_path
        logger.info(f"Extracting dependencies from {tgz_filename}")
        dependencies, dep_error = extract_dependencies(save_path)
        if dep_error:
            logger.error(f"Dependency extraction failed for {name}#{version}: {dep_error}")
            results['errors'].append(f"Dependency extraction failed for {name}#{version}: {dep_error}")
            results['processed'].add(package_id_tuple)
            continue
        elif dependencies is None:
            logger.error(f"Critical error in dependency extraction for {name}#{version}")
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
                        logger.info(f"Queueing dependency {dep_name}#{dep_version} (recursive mode)")
                    elif dependency_mode == 'patch-canonical' and dep_tuple == CANONICAL_PACKAGE:
                        should_queue = True
                        logger.info(f"Queueing canonical dependency {dep_name}#{dep_version} (patch-canonical mode)")
                    if should_queue:
                        logger.debug(f"Adding dependency to queue ({dependency_mode}): {dep_name}#{dep_version}")
                        pending_queue.append(dep_tuple)
                        queued_or_processed_lookup.add(dep_tuple)
        logger.info(f"Saving metadata for {name}#{version}")
        save_package_metadata(name, version, dependency_mode, current_package_deps)
        if dependency_mode == 'tree-shaking' and package_id_tuple == (initial_name, initial_version):
            logger.info(f"Performing tree-shaking for {initial_name}#{initial_version}")
            used_types = extract_used_types(save_path)
            if used_types:
                type_to_package = map_types_to_packages(used_types, results['all_dependencies'], download_dir)
                tree_shaken_deps = set(type_to_package.values()) - {package_id_tuple}
                if CANONICAL_PACKAGE not in tree_shaken_deps:
                    tree_shaken_deps.add(CANONICAL_PACKAGE)
                    logger.info(f"Ensuring canonical package {CANONICAL_PACKAGE[0]}#{CANONICAL_PACKAGE[1]} for tree-shaking")
                for dep_tuple in tree_shaken_deps:
                    if dep_tuple not in queued_or_processed_lookup:
                        logger.info(f"Queueing tree-shaken dependency {dep_tuple[0]}#{dep_tuple[1]}")
                        pending_queue.append(dep_tuple)
                        queued_or_processed_lookup.add(dep_tuple)
    results['dependencies'] = [{"name": d[0], "version": d[1]} for d in all_found_dependencies]
    logger.info(f"Completed import of {initial_name}#{initial_version}. Processed {len(results['processed'])} packages, downloaded {len(results['downloaded'])}, with {len(results['errors'])} errors")
    return results

# --- Validation Route ---
@services_bp.route('/validate-sample', methods=['POST'])
@swag_from({
    'tags': ['Validation'],
    'summary': 'Validate a FHIR resource or bundle.',
    'description': 'Validates a given FHIR resource or bundle against profiles defined in a specified FHIR package. Uses HAPI FHIR for validation if a profile is specified, otherwise falls back to local StructureDefinition checks.',
    'security': [{'ApiKeyAuth': []}], # Assuming API key is desired
    'consumes': ['application/json'],
    'parameters': [
        {
            'name': 'validation_payload', # Changed name
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'required': ['package_name', 'version', 'sample_data'],
                'properties': {
                    'package_name': {'type': 'string', 'example': 'hl7.fhir.us.core'},
                    'version': {'type': 'string', 'example': '6.1.0'},
                    'sample_data': {'type': 'string', 'description': 'A JSON string of the FHIR resource or Bundle to validate.'},
                    # 'include_dependencies': {'type': 'boolean', 'default': True} # This seems to be a server-side decision now
                }
            }
        }
    ],
    'responses': {
        '200': {
            'description': 'Validation result.',
            'schema': { # Define the schema of the validation_result dictionary
                'type': 'object',
                'properties': {
                    'valid': {'type': 'boolean'},
                    'errors': {'type': 'array', 'items': {'type': 'string'}},
                    'warnings': {'type': 'array', 'items': {'type': 'string'}},
                    'details': {'type': 'array', 'items': {'type': 'object'}}, # more specific if known
                    'resource_type': {'type': 'string'},
                    'resource_id': {'type': 'string'},
                    'profile': {'type': 'string', 'nullable': True},
                    'summary': {'type': 'object'}
                }
            }
        },
        '400': {'description': 'Invalid request (e.g., missing fields, invalid JSON).'},
        '404': {'description': 'Specified package for validation not found.'},
        '500': {'description': 'Server error during validation.'}
    }
})
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

# --- ADD THIS NEW FUNCTION TO services.py ---
def find_and_extract_search_params(tgz_path, base_resource_type):
    """Finds and extracts SearchParameter resources relevant to a given base resource type from a FHIR package tgz file."""
    search_params = []
    if not tgz_path or not os.path.exists(tgz_path):
        logger.error(f"Package file not found for SearchParameter extraction: {tgz_path}")
        return search_params
    logger.debug(f"Searching for SearchParameters based on '{base_resource_type}' in {os.path.basename(tgz_path)}")
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
                        if isinstance(data, dict) and data.get('resourceType') == 'SearchParameter':
                            sp_bases = data.get('base', [])
                            if base_resource_type in sp_bases:
                                param_info = {
                                    'id': data.get('id'),
                                    'url': data.get('url'),
                                    'name': data.get('name'),
                                    'description': data.get('description'),
                                    'code': data.get('code'),
                                    'type': data.get('type'),
                                    'expression': data.get('expression'),
                                    'base': sp_bases,
                                    'conformance': 'N/A',
                                    'is_mandatory': False
                                }
                                search_params.append(param_info)
                                logger.debug(f"Found relevant SearchParameter: {param_info.get('name')} (ID: {param_info.get('id')}) for base {base_resource_type}")
                except json.JSONDecodeError as e:
                    logger.debug(f"Could not parse JSON for SearchParameter in {member.name}, skipping: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Could not decode UTF-8 for SearchParameter in {member.name}, skipping: {e}")
                except tarfile.TarError as e:
                    logger.warning(f"Tar error reading member {member.name} for SearchParameter, skipping: {e}")
                except Exception as e:
                    logger.warning(f"Could not read/parse potential SearchParameter {member.name}, skipping: {e}", exc_info=False)
                finally:
                    if fileobj:
                        fileobj.close()
    except tarfile.ReadError as e:
        logger.error(f"Tar ReadError extracting SearchParameters from {tgz_path}: {e}")
    except tarfile.TarError as e:
        logger.error(f"TarError extracting SearchParameters from {tgz_path}: {e}")
    except FileNotFoundError:
        logger.error(f"Package file not found during SearchParameter extraction: {tgz_path}")
    except Exception as e:
        logger.error(f"Unexpected error extracting SearchParameters from {tgz_path}: {e}", exc_info=True)
    logger.info(f"Found {len(search_params)} SearchParameters relevant to '{base_resource_type}' in {os.path.basename(tgz_path)}")
    return search_params
# --- END OF NEW FUNCTION ---

# --- Full Replacement Function (Corrected Prefix Definitions & Unabbreviated) ---

def generate_push_stream(package_name, version, fhir_server_url, include_dependencies,
                         auth_type, auth_token, resource_types_filter, skip_files,
                         dry_run, verbose, force_upload, packages_dir):
    """
    Generates NDJSON stream for the push IG operation.
    Handles canonical resources (search by URL, POST/PUT),
    skips identical resources (unless force_upload is true), and specified files.
    """
    # --- Variable Initializations ---
    pushed_packages_info = []
    success_count = 0
    failure_count = 0
    skipped_count = 0
    post_count = 0
    put_count = 0
    total_resources_attempted = 0
    processed_resources = set()
    failed_uploads_details = []
    skipped_resources_details = []
    filter_set = set(resource_types_filter) if resource_types_filter else None
    skip_files_set = set(skip_files) if skip_files else set()

    try:
        # --- Start Messages ---
        operation_mode = " (DRY RUN)" if dry_run else ""
        force_mode = " (FORCE UPLOAD)" if force_upload else ""
        yield json.dumps({"type": "start", "message": f"Starting push{operation_mode}{force_mode} for {package_name}#{version} to {fhir_server_url}"}) + "\n"
        if filter_set:
            yield json.dumps({"type": "info", "message": f"Filtering for resource types: {', '.join(sorted(list(filter_set)))}"}) + "\n"
        if skip_files_set:
            yield json.dumps({"type": "info", "message": f"Skipping {len(skip_files_set)} specific files."}) + "\n"
        yield json.dumps({"type": "info", "message": f"Include Dependencies: {'Yes' if include_dependencies else 'No'}"}) + "\n"

        # --- Define packages_to_push ---
        packages_to_push = []
        primary_tgz_filename = construct_tgz_filename(package_name, version)
        primary_tgz_path = os.path.join(packages_dir, primary_tgz_filename)

        if not os.path.exists(primary_tgz_path):
            yield json.dumps({"type": "error", "message": f"Primary package file not found: {primary_tgz_filename}"}) + "\n"
            raise FileNotFoundError(f"Primary package file not found: {primary_tgz_path}")

        packages_to_push.append((package_name, version, primary_tgz_path))
        logger.debug(f"Added primary package to push list: {package_name}#{version}")

        if include_dependencies:
            yield json.dumps({"type": "info", "message": "Including dependencies based on import metadata..."}) + "\n"
            metadata = get_package_metadata(package_name, version)
            if metadata and metadata.get("imported_dependencies"):
                dependencies_to_include = metadata["imported_dependencies"]
                logger.info(f"Found {len(dependencies_to_include)} dependencies in metadata to potentially include.")
                for dep in dependencies_to_include:
                    dep_name = dep.get("name")
                    dep_version = dep.get("version")
                    if dep_name and dep_version:
                        dep_tgz_filename = construct_tgz_filename(dep_name, dep_version)
                        dep_tgz_path = os.path.join(packages_dir, dep_tgz_filename)
                        if os.path.exists(dep_tgz_path):
                            if (dep_name, dep_version, dep_tgz_path) not in packages_to_push:
                                packages_to_push.append((dep_name, dep_version, dep_tgz_path))
                                logger.debug(f"Added dependency package to push list: {dep_name}#{dep_version}")
                        else:
                            yield json.dumps({"type": "warning", "message": f"Dependency package file not found, cannot include: {dep_tgz_filename}"}) + "\n"
                            logger.warning(f"Dependency package file listed in metadata but not found locally: {dep_tgz_path}")
            else:
                yield json.dumps({"type": "warning", "message": "Include Dependencies checked, but no dependency metadata found. Only pushing primary."}) + "\n"
                logger.warning(f"No dependency metadata found for {package_name}#{version} despite include_dependencies=True")

        # --- Resource Extraction & Filtering ---
        resources_to_upload = []
        seen_resource_files = set()

        for pkg_name, pkg_version, pkg_path in packages_to_push:
            yield json.dumps({"type": "progress", "message": f"Extracting resources from: {pkg_name}#{pkg_version}..."}) + "\n"
            try:
                with tarfile.open(pkg_path, "r:gz") as tar:
                    for member in tar.getmembers():
                        if not (member.isfile() and member.name.startswith("package/") and member.name.lower().endswith(".json")):
                            continue
                        basename_lower = os.path.basename(member.name).lower()
                        if basename_lower in ["package.json", ".index.json", "validation-summary.json", "validation-oo.json"]:
                            continue

                        normalized_member_name = member.name.replace("\\", "/")
                        if normalized_member_name in skip_files_set or member.name in skip_files_set:
                            if verbose:
                                yield json.dumps({"type": "info", "message": f"Skipping file due to filter: {member.name}"}) + "\n"
                            continue

                        if member.name in seen_resource_files:
                            if verbose:
                                yield json.dumps({"type": "info", "message": f"Skipping already seen file: {member.name}"}) + "\n"
                            continue
                        seen_resource_files.add(member.name)

                        try:
                            with tar.extractfile(member) as f:
                                resource_content = f.read().decode("utf-8-sig")
                                resource_data = json.loads(resource_content)

                                if isinstance(resource_data, dict) and "resourceType" in resource_data and "id" in resource_data:
                                    resource_type_val = resource_data.get("resourceType")
                                    if filter_set and resource_type_val not in filter_set:
                                        if verbose:
                                            yield json.dumps({"type": "info", "message": f"Skipping resource type {resource_type_val} due to filter: {member.name}"}) + "\n"
                                        continue
                                    resources_to_upload.append({
                                        "data": resource_data,
                                        "source_package": f"{pkg_name}#{pkg_version}",
                                        "source_filename": member.name
                                    })
                                else:
                                    yield json.dumps({"type": "warning", "message": f"Skipping invalid/incomplete resource structure in file: {member.name}"}) + "\n"
                        except json.JSONDecodeError as json_e:
                            yield json.dumps({"type": "warning", "message": f"JSON parse error in file {member.name}: {json_e}"}) + "\n"
                        except UnicodeDecodeError as uni_e:
                            yield json.dumps({"type": "warning", "message": f"Encoding error in file {member.name}: {uni_e}"}) + "\n"
                        except KeyError:
                            yield json.dumps({"type": "warning", "message": f"File not found within archive: {member.name}"}) + "\n"
                        except Exception as extract_e:
                            yield json.dumps({"type": "warning", "message": f"Error processing file {member.name}: {extract_e}"}) + "\n"
            except tarfile.ReadError as tar_read_e:
                error_msg = f"Tar ReadError reading package {pkg_name}#{pkg_version}: {tar_read_e}. Skipping package."
                yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                failure_count += 1
                failed_uploads_details.append({"resource": f"Package: {pkg_name}#{pkg_version}", "error": f"Read Error: {tar_read_e}"})
                continue
            except tarfile.TarError as tar_e:
                error_msg = f"TarError reading package {pkg_name}#{pkg_version}: {tar_e}. Skipping package."
                yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                failure_count += 1
                failed_uploads_details.append({"resource": f"Package: {pkg_name}#{pkg_version}", "error": f"Tar Error: {tar_e}"})
                continue
            except Exception as pkg_e:
                error_msg = f"Unexpected error reading package {pkg_name}#{pkg_version}: {pkg_e}. Skipping package."
                yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                failure_count += 1
                failed_uploads_details.append({"resource": f"Package: {pkg_name}#{pkg_version}", "error": f"Unexpected: {pkg_e}"})
                logger.error(f"Error reading package {pkg_path}: {pkg_e}", exc_info=True)
                continue

        total_resources_attempted = len(resources_to_upload)
        yield json.dumps({"type": "info", "message": f"Found {total_resources_attempted} resources matching filters across selected packages."}) + "\n"

        if total_resources_attempted == 0:
            yield json.dumps({"type": "warning", "message": "No resources found to upload after filtering."}) + "\n"
        else:
            # --- Resource Upload Loop Setup ---
            session = requests.Session()
            base_url = fhir_server_url.rstrip("/")
            headers = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
            # MODIFIED: Enhanced authentication handling
            if auth_type in ["bearerToken", "basic"] and auth_token:
                # Log the Authorization header (mask sensitive data)
                auth_display = "Basic <redacted>" if auth_type == "basic" else (auth_token[:10] + "..." if len(auth_token) > 10 else auth_token)
                yield json.dumps({"type": "info", "message": f"Using {auth_type} auth with header: Authorization: {auth_display}"}) + "\n"
                headers["Authorization"] = auth_token  # Use auth_token for both Bearer and Basic
            elif auth_type == "apiKey":
                internal_api_key = None
                try:
                    internal_api_key = current_app.config.get("API_KEY")
                except RuntimeError:
                    logger.warning("Cannot access current_app config outside of request context for API Key.")
                if internal_api_key:
                    headers["X-API-Key"] = internal_api_key
                    yield json.dumps({"type": "info", "message": "Using internal API Key authentication."}) + "\n"
                else:
                    yield json.dumps({"type": "warning", "message": "API Key auth selected, but no internal key configured/accessible."}) + "\n"
            else:
                yield json.dumps({"type": "info", "message": "Using no authentication."}) + "\n"

            # --- Main Upload Loop ---
            for i, resource_info in enumerate(resources_to_upload, 1):
                local_resource = resource_info["data"]
                source_pkg = resource_info["source_package"]
                resource_type = local_resource.get("resourceType")
                resource_id = local_resource.get("id")
                resource_log_id = f"{resource_type}/{resource_id}"
                canonical_url = local_resource.get("url")
                canonical_version = local_resource.get("version")
                is_canonical_type = resource_type in CANONICAL_RESOURCE_TYPES

                if resource_log_id in processed_resources:
                    if verbose:
                        yield json.dumps({"type": "info", "message": f"Skipping duplicate ID in processing list: {resource_log_id}"}) + "\n"
                    continue
                processed_resources.add(resource_log_id)

                if dry_run:
                    dry_run_action = "check/PUT"
                    if is_canonical_type and canonical_url:
                        dry_run_action = "search/POST/PUT"
                    yield json.dumps({"type": "progress", "message": f"[DRY RUN] Would {dry_run_action} {resource_log_id} ({i}/{total_resources_attempted}) from {source_pkg}"}) + "\n"
                    success_count += 1
                    pkg_found = False
                    for p in pushed_packages_info:
                        if p["id"] == source_pkg:
                            p["resource_count"] += 1
                            pkg_found = True
                            break
                    if not pkg_found:
                        pushed_packages_info.append({"id": source_pkg, "resource_count": 1})
                    continue

                existing_resource_id = None
                existing_resource_data = None
                action = "PUT"
                target_url = f"{base_url}/{resource_type}/{resource_id}"
                skip_resource = False

                if is_canonical_type and canonical_url:
                    action = "SEARCH_POST_PUT"
                    search_params = {"url": canonical_url}
                    if canonical_version:
                        search_params["version"] = canonical_version
                    search_url = f"{base_url}/{resource_type}"
                    if verbose:
                        yield json.dumps({"type": "info", "message": f"Canonical Type: Searching {search_url} with params {search_params}"}) + "\n"

                    try:
                        search_response = session.get(search_url, params=search_params, headers=headers, timeout=20)
                        search_response.raise_for_status()
                        search_bundle = search_response.json()

                        if search_bundle.get("resourceType") == "Bundle" and "entry" in search_bundle:
                            entries = search_bundle.get("entry", [])
                            if len(entries) == 1:
                                existing_resource_data = entries[0].get("resource")
                                if existing_resource_data:
                                    existing_resource_id = existing_resource_data.get("id")
                                    if existing_resource_id:
                                        action = "PUT"
                                        target_url = f"{base_url}/{resource_type}/{existing_resource_id}"
                                        if verbose:
                                            yield json.dumps({"type": "info", "message": f"Found existing canonical resource ID: {existing_resource_id}"}) + "\n"
                                    else:
                                        yield json.dumps({"type": "warning", "message": f"Found canonical {canonical_url}|{canonical_version} but lacks ID. Skipping update."}) + "\n"
                                        action = "SKIP"
                                        skip_resource = True
                                        skipped_count += 1
                                        skipped_resources_details.append({"resource": resource_log_id, "reason": "Found canonical match without ID"})
                                else:
                                    yield json.dumps({"type": "warning", "message": f"Search for {canonical_url}|{canonical_version} entry lacks resource data. Assuming not found."}) + "\n"
                                    action = "POST"
                                    target_url = f"{base_url}/{resource_type}"
                            elif len(entries) == 0:
                                action = "POST"
                                target_url = f"{base_url}/{resource_type}"
                                if verbose:
                                    yield json.dumps({"type": "info", "message": f"Canonical not found by URL/Version. Planning POST."}) + "\n"
                            else:
                                ids_found = [e.get("resource", {}).get("id", "unknown") for e in entries]
                                yield json.dumps({"type": "error", "message": f"Conflict: Found {len(entries)} matches for {canonical_url}|{canonical_version} (IDs: {', '.join(ids_found)}). Skipping."}) + "\n"
                                action = "SKIP"
                                skip_resource = True
                                failure_count += 1
                                failed_uploads_details.append({"resource": resource_log_id, "error": f"Conflict: Multiple matches ({len(entries)}) for canonical URL/Version"})
                        else:
                            yield json.dumps({"type": "warning", "message": f"Search for {canonical_url}|{canonical_version} returned non-Bundle/empty. Assuming not found."}) + "\n"
                            action = "POST"
                            target_url = f"{base_url}/{resource_type}"

                    except requests.exceptions.RequestException as search_err:
                        yield json.dumps({"type": "warning", "message": f"Search failed for {resource_log_id}: {search_err}. Defaulting to PUT by ID."}) + "\n"
                        action = "PUT"
                        target_url = f"{base_url}/{resource_type}/{resource_id}"
                    except json.JSONDecodeError as json_err:
                        yield json.dumps({"type": "warning", "message": f"Failed parse search result for {resource_log_id}: {json_err}. Defaulting PUT by ID."}) + "\n"
                        action = "PUT"
                        target_url = f"{base_url}/{resource_type}/{resource_id}"
                    except Exception as e:
                        yield json.dumps({"type": "warning", "message": f"Unexpected canonical search error for {resource_log_id}: {e}. Defaulting PUT by ID."}) + "\n"
                        action = "PUT"
                        target_url = f"{base_url}/{resource_type}/{resource_id}"

                if action == "PUT" and not force_upload and not skip_resource:
                    resource_to_compare = existing_resource_data
                    if not resource_to_compare:
                        try:
                            if verbose:
                                yield json.dumps({"type": "info", "message": f"Checking existing (PUT target): {target_url}"}) + "\n"
                            get_response = session.get(target_url, headers=headers, timeout=15)
                            if get_response.status_code == 200:
                                resource_to_compare = get_response.json()
                                if verbose:
                                    yield json.dumps({"type": "info", "message": f"Found resource by ID for comparison."}) + "\n"
                            elif get_response.status_code == 404:
                                if verbose:
                                    yield json.dumps({"type": "info", "message": f"Resource {resource_log_id} not found by ID ({target_url}). Proceeding with PUT create."}) + "\n"
                            else:
                                yield json.dumps({"type": "warning", "message": f"Comparison check failed (GET {get_response.status_code}). Attempting PUT."}) + "\n"
                        except Exception as get_err:
                            yield json.dumps({"type": "warning", "message": f"Comparison check failed (Error during GET by ID: {get_err}). Attempting PUT."}) + "\n"

                    if resource_to_compare:
                        try:
                            if are_resources_semantically_equal(local_resource, resource_to_compare):
                                yield json.dumps({"type": "info", "message": f"Skipping {resource_log_id} (Identical content)"}) + "\n"
                                skip_resource = True
                                skipped_count += 1
                                skipped_resources_details.append({"resource": resource_log_id, "reason": "Identical content"})
                            elif verbose:
                                yield json.dumps({"type": "info", "message": f"{resource_log_id} exists but differs. Updating."}) + "\n"
                        except Exception as comp_err:
                            yield json.dumps({"type": "warning", "message": f"Comparison failed for {resource_log_id}: {comp_err}. Proceeding with PUT."}) + "\n"

                elif action == "PUT" and force_upload:
                    if verbose:
                        yield json.dumps({"type": "info", "message": f"Force Upload enabled, skipping comparison for {resource_log_id}."}) + "\n"

                if not skip_resource:
                    http_method = action if action in ["POST", "PUT"] else "PUT"
                    log_action = f"{http_method}ing"
                    yield json.dumps({"type": "progress", "message": f"{log_action} {resource_log_id} ({i}/{total_resources_attempted}) to {target_url}..."}) + "\n"

                    try:
                        if http_method == "POST":
                            response = session.post(target_url, json=local_resource, headers=headers, timeout=30)
                            post_count += 1
                        else:
                            response = session.put(target_url, json=local_resource, headers=headers, timeout=30)
                            put_count += 1

                        response.raise_for_status()

                        success_msg = f"{http_method} successful for {resource_log_id} (Status: {response.status_code})"
                        if http_method == "POST" and response.status_code == 201:
                            location = response.headers.get("Location")
                            if location:
                                match = re.search(f"{resource_type}/([^/]+)/_history", location)
                                new_id = match.group(1) if match else "unknown"
                                success_msg += f" -> New ID: {new_id}"
                            else:
                                success_msg += " (No Location header)"
                        yield json.dumps({"type": "success", "message": success_msg}) + "\n"
                        success_count += 1
                        pkg_found_success = False
                        for p in pushed_packages_info:
                            if p["id"] == source_pkg:
                                p["resource_count"] += 1
                                pkg_found_success = True
                                break
                        if not pkg_found_success:
                            pushed_packages_info.append({"id": source_pkg, "resource_count": 1})

                    except requests.exceptions.HTTPError as http_err:
                        outcome_text = ""
                        status_code = http_err.response.status_code if http_err.response is not None else "N/A"
                        try:
                            outcome = http_err.response.json()
                            if outcome and outcome.get("resourceType") == "OperationOutcome":
                                issues = outcome.get("issue", [])
                                outcome_text = "; ".join([f"{i.get('severity', 'info')}: {i.get('diagnostics', i.get('details', {}).get('text', 'No details'))}" for i in issues]) if issues else "OperationOutcome with no issues."
                            else:
                                outcome_text = http_err.response.text[:200] if http_err.response is not None else "No response body"
                        except ValueError:
                            outcome_text = http_err.response.text[:200] if http_err.response is not None else "No response body (or not JSON)"
                        error_msg = f"Failed {http_method} {resource_log_id} (Status: {status_code}): {outcome_text or str(http_err)}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        failure_count += 1
                        failed_uploads_details.append({"resource": resource_log_id, "error": error_msg})
                    except requests.exceptions.Timeout:
                        error_msg = f"Timeout during {http_method} {resource_log_id}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        failure_count += 1
                        failed_uploads_details.append({"resource": resource_log_id, "error": "Timeout"})
                    except requests.exceptions.ConnectionError as conn_err:
                        error_msg = f"Connection error during {http_method} {resource_log_id}: {conn_err}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        failure_count += 1
                        failed_uploads_details.append({"resource": resource_log_id, "error": f"Connection Error: {conn_err}"})
                    except requests.exceptions.RequestException as req_err:
                        error_msg = f"Request error during {http_method} {resource_log_id}: {str(req_err)}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        failure_count += 1
                        failed_uploads_details.append({"resource": resource_log_id, "error": f"Request Error: {req_err}"})
                    except Exception as e:
                        error_msg = f"Unexpected error during {http_method} {resource_log_id}: {str(e)}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        failure_count += 1
                        failed_uploads_details.append({"resource": resource_log_id, "error": f"Unexpected: {e}"})
                        logger.error(f"[API Push Stream] Upload error for {resource_log_id}: {e}", exc_info=True)
                else:
                    pkg_found_skipped = False
                    for p in pushed_packages_info:
                        if p["id"] == source_pkg:
                            pkg_found_skipped = True
                            break
                    if not pkg_found_skipped:
                        pushed_packages_info.append({"id": source_pkg, "resource_count": 0})

        # --- Final Summary ---
        final_status = "success" if failure_count == 0 else "partial" if success_count > 0 else "failure"
        dry_run_prefix = "[DRY RUN] " if dry_run else ""
        force_prefix = "[FORCE UPLOAD] " if force_upload else ""
        if total_resources_attempted == 0 and failure_count == 0:
            summary_message = f"{dry_run_prefix}Push finished: No matching resources found to process."
            final_status = "success"
        else:
            summary_message = f"{dry_run_prefix}{force_prefix}Push finished: {post_count} POSTed, {put_count} PUT, {failure_count} failed, {skipped_count} skipped ({total_resources_attempted} resources attempted)."

        summary = {
            "status": final_status,
            "message": summary_message,
            "target_server": fhir_server_url,
            "package_name": package_name,
            "version": version,
            "included_dependencies": include_dependencies,
            "resources_attempted": total_resources_attempted,
            "success_count": success_count,
            "post_count": post_count,
            "put_count": put_count,
            "failure_count": failure_count,
            "skipped_count": skipped_count,
            "validation_failure_count": 0,
            "failed_details": failed_uploads_details,
            "skipped_details": skipped_resources_details,
            "pushed_packages_summary": pushed_packages_info,
            "dry_run": dry_run,
            "force_upload": force_upload,
            "resource_types_filter": resource_types_filter,
            "skip_files_filter": sorted(list(skip_files_set)) if skip_files_set else None
        }
        yield json.dumps({"type": "complete", "data": summary}) + "\n"
        logger.info(f"[API Push Stream] Completed {package_name}#{version}. Status: {final_status}. {summary_message}")

    except FileNotFoundError as fnf_err:
        logger.error(f"[API Push Stream] Setup error: {str(fnf_err)}", exc_info=False)
        error_response = {"status": "error", "message": f"Setup error: {str(fnf_err)}"}
        try:
            yield json.dumps({"type": "error", "message": error_response["message"]}) + "\n"
            yield json.dumps({"type": "complete", "data": error_response}) + "\n"
        except Exception as yield_e:
            logger.error(f"Error yielding final setup error: {yield_e}")
    except Exception as e:
        logger.error(f"[API Push Stream] Critical error during setup or stream generation: {str(e)}", exc_info=True)
        error_response = {"status": "error", "message": f"Server error during push setup: {str(e)}"}
        try:
            yield json.dumps({"type": "error", "message": error_response["message"]}) + "\n"
            yield json.dumps({"type": "complete", "data": error_response}) + "\n"
        except Exception as yield_e:
            logger.error(f"Error yielding final critical error: {yield_e}")

# --- END generate_push_stream FUNCTION ---

def are_resources_semantically_equal(resource1, resource2):
    """
    Compares two FHIR resources, ignoring metadata like versionId, lastUpdated,
    source, and the text narrative.
    Logs differing JSON strings if comparison fails and DeepDiff is unavailable.
    Returns True if they are semantically equal, False otherwise.
    """
    if not isinstance(resource1, dict) or not isinstance(resource2, dict):
        return False
    if resource1.get('resourceType') != resource2.get('resourceType'):
        # Log difference if needed, or just return False
        # logger.debug(f"Resource types differ: {resource1.get('resourceType')} vs {resource2.get('resourceType')}")
        return False

    # Create deep copies to avoid modifying the originals
    try:
        copy1 = json.loads(json.dumps(resource1))
        copy2 = json.loads(json.dumps(resource2))
    except Exception as e:
        logger.error(f"Compare Error: Failed deep copy: {e}")
        return False # Cannot compare if copying fails

    # Keys to ignore within the 'meta' tag during comparison
    # --- UPDATED: Added 'source' to the list ---
    keys_to_ignore_in_meta = ['versionId', 'lastUpdated', 'source']
    # --- END UPDATE ---

    # Remove meta fields to ignore from copy1
    if 'meta' in copy1:
        for key in keys_to_ignore_in_meta:
            copy1['meta'].pop(key, None)
        # Remove meta tag entirely if it's now empty
        if not copy1['meta']:
            copy1.pop('meta', None)

    # Remove meta fields to ignore from copy2
    if 'meta' in copy2:
        for key in keys_to_ignore_in_meta:
            copy2['meta'].pop(key, None)
        # Remove meta tag entirely if it's now empty
        if not copy2['meta']:
            copy2.pop('meta', None)

    # Remove narrative text element from both copies
    copy1.pop('text', None)
    copy2.pop('text', None)

    # --- Comparison ---
    try:
        # Convert cleaned copies to sorted, indented JSON strings for comparison & logging
        # Using indent=2 helps readability when logging the strings.
        json_str1 = json.dumps(copy1, sort_keys=True, indent=2)
        json_str2 = json.dumps(copy2, sort_keys=True, indent=2)

        # Perform the comparison
        are_equal = (json_str1 == json_str2)

        # --- Debug Logging if Comparison Fails ---
        if not are_equal:
            resource_id = resource1.get('id', 'UNKNOWN_ID') # Get ID safely
            resource_type = resource1.get('resourceType', 'UNKNOWN_TYPE') # Get Type safely
            log_prefix = f"Comparison Failed for {resource_type}/{resource_id} (after ignoring meta.source)"
            logger.debug(log_prefix)

            # Attempt to use DeepDiff for a structured difference report
            try:
                 from deepdiff import DeepDiff
                 # Configure DeepDiff for potentially better comparison
                 # ignore_order=True is important for lists/arrays
                 # significant_digits might help with float issues if needed
                 # report_repetition=True might help spot array differences
                 diff = DeepDiff(copy1, copy2, ignore_order=True, report_repetition=True, verbose_level=0)
                 # Only log if diff is not empty
                 if diff:
                    logger.debug(f"DeepDiff details: {diff}")
                 else:
                    # This case suggests deepdiff found them equal but string comparison failed - odd.
                    logger.debug(f"JSON strings differed, but DeepDiff found no differences.")
                    # Log JSON strings if deepdiff shows no difference (or isn't available)
                    logger.debug(f"--- {resource_type}/{resource_id} Resource 1 (Local/Cleaned) --- START ---")
                    logger.debug(json_str1)
                    logger.debug(f"--- {resource_type}/{resource_id} Resource 1 (Local/Cleaned) --- END ---")
                    logger.debug(f"--- {resource_type}/{resource_id} Resource 2 (Server/Cleaned) --- START ---")
                    logger.debug(json_str2)
                    logger.debug(f"--- {resource_type}/{resource_id} Resource 2 (Server/Cleaned) --- END ---")

            except ImportError:
                 # DeepDiff not available, log the differing JSON strings
                 logger.debug(f"DeepDiff not available. Logging differing JSON strings.")
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 1 (Local/Cleaned) --- START ---")
                 logger.debug(json_str1)
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 1 (Local/Cleaned) --- END ---")
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 2 (Server/Cleaned) --- START ---")
                 logger.debug(json_str2)
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 2 (Server/Cleaned) --- END ---")
            except Exception as diff_err:
                 # Error during deepdiff itself
                 logger.error(f"Error during deepdiff calculation for {resource_type}/{resource_id}: {diff_err}")
                 # Fallback to logging JSON strings
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 1 (Local/Cleaned) --- START ---")
                 logger.debug(json_str1)
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 1 (Local/Cleaned) --- END ---")
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 2 (Server/Cleaned) --- START ---")
                 logger.debug(json_str2)
                 logger.debug(f"--- {resource_type}/{resource_id} Resource 2 (Server/Cleaned) --- END ---")

        # --- END DEBUG LOGGING ---

        return are_equal

    except Exception as e:
         # Catch errors during JSON dumping or final comparison steps
         resource_id_err = resource1.get('id', 'UNKNOWN_ID')
         resource_type_err = resource1.get('resourceType', 'UNKNOWN_TYPE')
         logger.error(f"Error during final comparison step for {resource_type_err}/{resource_id_err}: {e}", exc_info=True)
         return False # Treat comparison errors as 'not equal' to be safe
# --- END FUNCTION ---

# --- Service Function for Test Data Upload (with Conditional Upload) ---
def process_and_upload_test_data(server_info, options, temp_file_dir):
    """
    Parses test data files, optionally validates, builds dependency graph,
    sorts, and uploads resources individually (conditionally or simple PUT) or as a transaction bundle.
    Yields NDJSON progress updates.
    """
    files_processed_count = 0
    resource_map = {}
    error_count = 0
    errors = []
    processed_filenames = set()
    verbose = True
    resources_uploaded_count = 0
    resources_parsed_list = []
    sorted_resources_ids = []
    validation_errors_count = 0
    validation_warnings_count = 0
    validation_failed_resources = set()
    adj = defaultdict(list)
    rev_adj = defaultdict(list)
    in_degree = defaultdict(int)
    nodes = set()

    try:
        yield json.dumps({"type": "progress", "message": f"Scanning upload directory..."}) + "\n"

        # --- 1. List and Process Files ---
        files_to_parse = []
        initial_files = [os.path.join(temp_file_dir, f) for f in os.listdir(temp_file_dir) if os.path.isfile(os.path.join(temp_file_dir, f))]
        files_processed_count = len(initial_files)
        for file_path in initial_files:
            filename = os.path.basename(file_path)
            if filename.lower().endswith('.zip'):
                yield json.dumps({"type": "progress", "message": f"Extracting ZIP: {filename}..."}) + "\n"
                try:
                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        extracted_count = 0
                        for member in zip_ref.namelist():
                            if member.endswith('/') or member.startswith('__MACOSX') or member.startswith('.'): continue
                            member_filename = os.path.basename(member)
                            if not member_filename: continue
                            if member_filename.lower().endswith(('.json', '.xml')):
                                target_path = os.path.join(temp_file_dir, member_filename)
                                if not os.path.exists(target_path):
                                    with zip_ref.open(member) as source, open(target_path, "wb") as target:
                                        shutil.copyfileobj(source, target)
                                    files_to_parse.append(target_path)
                                    extracted_count += 1
                                else:
                                    yield json.dumps({"type": "warning", "message": f"Skipped extracting '{member_filename}' from ZIP, file exists."}) + "\n"
                        yield json.dumps({"type": "info", "message": f"Extracted {extracted_count} JSON/XML files from {filename}."}) + "\n"
                        processed_filenames.add(filename)
                except zipfile.BadZipFile:
                    error_msg = f"Invalid ZIP: {filename}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    errors.append(error_msg)
                    error_count += 1
                except Exception as e:
                    error_msg = f"Error extracting ZIP {filename}: {e}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    errors.append(error_msg)
                    error_count += 1
            elif filename.lower().endswith(('.json', '.xml')):
                files_to_parse.append(file_path)
        yield json.dumps({"type": "info", "message": f"Found {len(files_to_parse)} JSON/XML files to parse."}) + "\n"

        # --- 2. Parse JSON/XML Files ---
        temp_resources_parsed = []
        for file_path in files_to_parse:
            filename = os.path.basename(file_path)
            if filename in processed_filenames:
                continue
            processed_filenames.add(filename)
            yield json.dumps({"type": "progress", "message": f"Parsing {filename}..."}) + "\n"
            try:
                with open(file_path, 'r', encoding='utf-8-sig') as f:
                    content = f.read()
                parsed_content_list = []
                if filename.lower().endswith('.json'):
                    try:
                        parsed_json = json.loads(content)
                        if isinstance(parsed_json, dict) and parsed_json.get('resourceType') == 'Bundle':
                            for entry_idx, entry in enumerate(parsed_json.get('entry', [])):
                                resource = entry.get('resource')
                                if isinstance(resource, dict) and 'resourceType' in resource and 'id' in resource:
                                    parsed_content_list.append(resource)
                                elif resource:
                                    yield json.dumps({"type": "warning", "message": f"Skipping invalid resource #{entry_idx+1} in Bundle {filename}."}) + "\n"
                        elif isinstance(parsed_json, dict) and 'resourceType' in parsed_json and 'id' in parsed_json:
                            parsed_content_list.append(parsed_json)
                        elif isinstance(parsed_json, list):
                            yield json.dumps({"type": "warning", "message": f"File {filename} contains JSON array."}) + "\n"
                            for item_idx, item in enumerate(parsed_json):
                                if isinstance(item, dict) and 'resourceType' in item and 'id' in item:
                                    parsed_content_list.append(item)
                                else:
                                    yield json.dumps({"type": "warning", "message": f"Skipping invalid item #{item_idx+1} in JSON array {filename}."}) + "\n"
                        else:
                            raise ValueError("Not valid FHIR Resource/Bundle.")
                    except json.JSONDecodeError as e:
                        raise ValueError(f"Invalid JSON: {e}")
                elif filename.lower().endswith('.xml'):
                    if FHIR_RESOURCES_AVAILABLE:
                        try:
                            root = ET.fromstring(content)
                            resource_type = root.tag
                            if not resource_type:
                                raise ValueError("XML root tag missing.")
                            temp_dict = basic_fhir_xml_to_dict(content)
                            if temp_dict:
                                model_class = get_fhir_model_class(resource_type)
                                fhir_resource = model_class(**temp_dict)
                                resource_dict = fhir_resource.dict(exclude_none=True)
                                if 'id' in resource_dict:
                                    parsed_content_list.append(resource_dict)
                                    yield json.dumps({"type": "info", "message": f"Parsed/validated XML: {filename}"}) + "\n"
                                else:
                                    yield json.dumps({"type": "warning", "message": f"Parsed XML {filename} missing 'id'. Skipping."}) + "\n"
                            else:
                                raise ValueError("Basic XML to Dict failed.")
                        except (ET.ParseError, FHIRValidationError, ValueError, NotImplementedError, Exception) as e:
                            raise ValueError(f"Invalid/Unsupported FHIR XML: {e}")
                    else:
                        parsed_content = basic_fhir_xml_to_dict(content)
                        if parsed_content and parsed_content.get("resourceType") and parsed_content.get("id"):
                            yield json.dumps({"type": "warning", "message": f"Parsed basic XML (no validation): {filename}"}) + "\n"
                            parsed_content_list.append(parsed_content)
                        else:
                            yield json.dumps({"type": "warning", "message": f"Basic XML parse failed or missing type/id: {filename}. Skipping."}) + "\n"
                            continue
                if parsed_content_list:
                    temp_resources_parsed.extend(parsed_content_list)
                else:
                    yield json.dumps({"type": "warning", "message": f"Skipping {filename}: No valid content."}) + "\n"
            except (IOError, ValueError, Exception) as e:
                error_msg = f"Error processing file {filename}: {e}"
                yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                errors.append(error_msg)
                error_count += 1
                logger.error(f"Error processing file {filename}", exc_info=True)

        # Populate Resource Map
        for resource in temp_resources_parsed:
            res_type = resource.get('resourceType')
            res_id = resource.get('id')
            if res_type and res_id:
                full_id = f"{res_type}/{res_id}"
                if full_id not in resource_map:
                    resource_map[full_id] = resource
                else:
                    yield json.dumps({"type": "warning", "message": f"Duplicate ID: {full_id}. Using first."}) + "\n"
            else:
                yield json.dumps({"type": "warning", "message": f"Parsed resource missing type/id: {str(resource)[:100]}..."}) + "\n"
        resources_parsed_list = list(resource_map.values())
        yield json.dumps({"type": "info", "message": f"Parsed {len(resources_parsed_list)} unique resources."}) + "\n"

        # --- 2.5 Pre-Upload Validation Step ---
        if options.get('validate_before_upload'):
            validation_package_id = options.get('validation_package_id')
            if not validation_package_id or '#' not in validation_package_id:
                raise ValueError("Validation package ID missing/invalid.")
            val_pkg_name, val_pkg_version = validation_package_id.split('#', 1)
            yield json.dumps({"type": "progress", "message": f"Starting validation against {val_pkg_name}#{val_pkg_version}..."}) + "\n"
            validated_resources_map = {}
            for resource in resources_parsed_list:
                full_id = f"{resource.get('resourceType')}/{resource.get('id')}"
                yield json.dumps({"type": "validation_info", "message": f"Validating {full_id}..."}) + "\n"
                try:
                    validation_report = validate_resource_against_profile(val_pkg_name, val_pkg_version, resource, include_dependencies=False)
                    for warning in validation_report.get('warnings', []):
                        yield json.dumps({"type": "validation_warning", "message": f"{full_id}: {warning}"}) + "\n"
                        validation_warnings_count += 1
                    if not validation_report.get('valid', False):
                        validation_failed_resources.add(full_id)
                        validation_errors_count += 1
                        for error in validation_report.get('errors', []):
                            error_detail = f"Validation Error ({full_id}): {error}"
                            yield json.dumps({"type": "validation_error", "message": error_detail}) + "\n"
                            errors.append(error_detail)
                        if options.get('error_handling', 'stop') == 'stop':
                            raise ValueError(f"Validation failed for {full_id} (stop on error).")
                    else:
                        validated_resources_map[full_id] = resource
                except Exception as val_err:
                    error_msg = f"Validation error {full_id}: {val_err}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    errors.append(error_msg)
                    error_count += 1
                    validation_failed_resources.add(full_id)
                    validation_errors_count += 1
                    logger.error(f"Validation exception {full_id}", exc_info=True)
                    if options.get('error_handling', 'stop') == 'stop':
                        raise ValueError(f"Validation exception for {full_id} (stop on error).")
            yield json.dumps({"type": "info", "message": f"Validation complete. Errors: {validation_errors_count}, Warnings: {validation_warnings_count}."}) + "\n"
            resource_map = validated_resources_map
            nodes = set(resource_map.keys())
            yield json.dumps({"type": "info", "message": f"Proceeding with {len(nodes)} valid resources."}) + "\n"
        else:
            yield json.dumps({"type": "info", "message": "Pre-upload validation skipped."}) + "\n"
            nodes = set(resource_map.keys())

        # --- 3. Build Dependency Graph ---
        yield json.dumps({"type": "progress", "message": "Building dependency graph..."}) + "\n"
        dependency_count = 0
        external_refs = defaultdict(list)
        for full_id, resource in resource_map.items():
            refs_list = []
            find_references(resource, refs_list)
            if refs_list:
                if verbose:
                    yield json.dumps({"type": "info", "message": f"Processing {len(refs_list)} refs in {full_id}"}) + "\n"
                for ref_str in refs_list:
                    target_full_id = None
                    if isinstance(ref_str, str) and '/' in ref_str and not ref_str.startswith('#'):
                        parts = ref_str.split('/')
                        if len(parts) == 2 and parts[0] and parts[1]:
                            target_full_id = ref_str
                        elif len(parts) > 2:
                            try:
                                parsed_url = urlparse(ref_str)
                                if parsed_url.path:
                                    path_parts = parsed_url.path.strip('/').split('/')
                                    if len(path_parts) >= 2 and path_parts[-2] and path_parts[-1]:
                                        target_full_id = f"{path_parts[-2]}/{path_parts[-1]}"
                            except:
                                pass
                    if target_full_id and target_full_id != full_id:
                        if target_full_id in resource_map:
                            if target_full_id not in adj[full_id]:
                                adj[full_id].append(target_full_id)
                                rev_adj[target_full_id].append(full_id)
                                in_degree[full_id] += 1
                                dependency_count += 1
                                if verbose:
                                    yield json.dumps({"type": "info", "message": f"  Dep Added: {full_id} -> {target_full_id}"}) + "\n"
                        else:
                            target_failed_validation = options.get('validate_before_upload') and target_full_id in validation_failed_resources
                            if not target_failed_validation and verbose:
                                yield json.dumps({"type": "warning", "message": f"Ref '{ref_str}' in {full_id} points outside processed set ({target_full_id})."}) + "\n"
                            external_refs[full_id].append(ref_str)
        yield json.dumps({"type": "info", "message": f"Graph built for {len(nodes)} resources. Internal Deps: {dependency_count}."}) + "\n"

        # --- 4. Perform Topological Sort ---
        yield json.dumps({"type": "progress", "message": "Sorting resources by dependency..."}) + "\n"
        sorted_resources_ids = []
        queue = deque([node for node in nodes if in_degree[node] == 0])
        processed_count = 0
        while queue:
            u = queue.popleft()
            sorted_resources_ids.append(u)
            processed_count += 1
            if u in rev_adj:
                for v in rev_adj[u]:
                    in_degree[v] -= 1
                    if in_degree[v] == 0:
                        queue.append(v)
        if processed_count != len(nodes):
            cycle_nodes = sorted([node for node in nodes if in_degree[node] > 0])
            error_msg = f"Circular dependency detected. Involved: {', '.join(cycle_nodes[:10])}{'...' if len(cycle_nodes) > 10 else ''}"
            yield json.dumps({"type": "error", "message": error_msg}) + "\n"
            errors.append(error_msg)
            error_count += 1
            raise ValueError("Circular dependency detected")
        yield json.dumps({"type": "info", "message": f"Topological sort successful. Order determined for {len(sorted_resources_ids)} resources."}) + "\n"

        # --- 5. Upload Sorted Resources ---
        if not sorted_resources_ids:
            yield json.dumps({"type": "info", "message": "No valid resources remaining to upload."}) + "\n"
        else:
            upload_mode = options.get('upload_mode', 'individual')
            error_handling_mode = options.get('error_handling', 'stop')
            use_conditional = options.get('use_conditional_uploads', False) and upload_mode == 'individual'
            session = requests.Session()
            base_url = server_info['url'].rstrip('/')
            upload_headers = {'Content-Type': 'application/fhir+json', 'Accept': 'application/fhir+json'}
            if server_info['auth_type'] in ['bearerToken', 'basic'] and server_info.get('auth_token'):
                # Log the Authorization header (mask sensitive data)
                auth_header = server_info['auth_token']
                if auth_header.startswith('Basic '):
                    auth_display = 'Basic <redacted>'
                else:
                    auth_display = auth_header[:10] + '...' if len(auth_header) > 10 else auth_header
                yield json.dumps({"type": "info", "message": f"Using {server_info['auth_type']} auth with header: Authorization: {auth_display}"}) + "\n"
                upload_headers['Authorization'] = server_info['auth_token']  # FIXED: Use server_info['auth_token']
            else:
                yield json.dumps({"type": "info", "message": "Using no auth."}) + "\n"

            if upload_mode == 'transaction':
                # --- Transaction Bundle Upload ---
                yield json.dumps({"type": "progress", "message": f"Preparing transaction bundle for {len(sorted_resources_ids)} resources..."}) + "\n"
                transaction_bundle = {"resourceType": "Bundle", "type": "transaction", "entry": []}
                for full_id in sorted_resources_ids:
                    resource = resource_map.get(full_id)
                    if resource:
                        res_type = resource.get('resourceType')
                        res_id = resource.get('id')
                        entry = {
                            "fullUrl": f"{base_url}/{res_type}/{res_id}",
                            "resource": resource,
                            "request": {"method": "PUT", "url": f"{res_type}/{res_id}"}
                        }
                        transaction_bundle["entry"].append(entry)
                if not transaction_bundle["entry"]:
                    yield json.dumps({"type": "warning", "message": "No valid entries for transaction."}) + "\n"
                else:
                    yield json.dumps({"type": "progress", "message": f"Uploading transaction bundle ({len(transaction_bundle['entry'])} entries)..."}) + "\n"
                    try:
                        response = session.post(base_url, json=transaction_bundle, headers=upload_headers, timeout=120)
                        response.raise_for_status()
                        response_bundle = response.json()
                        current_bundle_success = 0
                        current_bundle_errors = 0
                        for entry in response_bundle.get("entry", []):
                            entry_response = entry.get("response", {})
                            status = entry_response.get("status", "")
                            location = entry_response.get("location", "N/A")
                            resource_ref = location.split('/')[-3] + '/' + location.split('/')[-1] if status.startswith("201") and '/_history/' in location else location
                            if status.startswith("200") or status.startswith("201"):
                                current_bundle_success += 1
                            else:
                                current_bundle_errors += 1
                                outcome = entry.get("resource")
                                outcome_text = f"Status: {status}"
                                if outcome and outcome.get('resourceType') == 'OperationOutcome':
                                    try:
                                        issue_texts = []
                                        for issue in outcome.get('issue', []):
                                            severity = issue.get('severity', 'info')
                                            diag = issue.get('diagnostics') or issue.get('details', {}).get('text', 'No details')
                                            issue_texts.append(f"{severity}: {diag}")
                                        if issue_texts:
                                            outcome_text += "; " + "; ".join(issue_texts)
                                    except Exception as parse_err:
                                        logger.warning(f"Could not parse OperationOutcome details: {parse_err}")
                                error_msg = f"Txn entry failed for '{resource_ref}'. {outcome_text}"
                                yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                                errors.append(error_msg)
                                if error_handling_mode == 'stop':
                                    break
                        resources_uploaded_count += current_bundle_success
                        error_count += current_bundle_errors
                        yield json.dumps({"type": "success", "message": f"Txn processed. Success: {current_bundle_success}, Errors: {current_bundle_errors}."}) + "\n"
                        if current_bundle_errors > 0 and error_handling_mode == 'stop':
                            raise ValueError("Stopping due to transaction error.")
                    except requests.exceptions.HTTPError as e:
                        outcome_text = ""
                        if e.response is not None:
                            try:
                                outcome = e.response.json()
                                if outcome and outcome.get('resourceType') == 'OperationOutcome':
                                    issue_texts = []
                                    for issue in outcome.get('issue', []):
                                        severity = issue.get('severity', 'info')
                                        diag = issue.get('diagnostics') or issue.get('details', {}).get('text', 'No details')
                                        issue_texts.append(f"{severity}: {diag}")
                                    if issue_texts:
                                        outcome_text = "; ".join(issue_texts)
                                    else:
                                        outcome_text = e.response.text[:300]
                                else:
                                    outcome_text = e.response.text[:300]
                            except ValueError:
                                outcome_text = e.response.text[:300]
                        else:
                            outcome_text = "No response body."
                        error_msg = f"Txn POST failed (Status: {e.response.status_code if e.response is not None else 'N/A'}): {outcome_text or str(e)}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(error_msg)
                        error_count += len(transaction_bundle["entry"])
                        raise ValueError("Stopping due to transaction POST error.")
                    except requests.exceptions.RequestException as e:
                        error_msg = f"Network error posting txn: {e}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(error_msg)
                        error_count += len(transaction_bundle["entry"])
                        raise ValueError("Stopping due to transaction network error.")
                    except Exception as e:
                        error_msg = f"Error processing txn response: {e}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(error_msg)
                        error_count += len(transaction_bundle["entry"])
                        logger.error("Txn response error", exc_info=True)
                        raise ValueError("Stopping due to txn response error.")

            else:
                # --- Individual Resource Upload ---
                yield json.dumps({"type": "progress", "message": f"Starting individual upload ({'conditional' if use_conditional else 'simple PUT'})..."}) + "\n"
                for i, full_id in enumerate(sorted_resources_ids):
                    resource_to_upload = resource_map.get(full_id)
                    if not resource_to_upload:
                        continue
                    res_type = resource_to_upload.get('resourceType')
                    res_id = resource_to_upload.get('id')
                    target_url_put = f"{base_url}/{res_type}/{res_id}"
                    target_url_post = f"{base_url}/{res_type}"

                    current_headers = upload_headers.copy()
                    action_log_prefix = f"Uploading {full_id} ({i+1}/{len(sorted_resources_ids)})"
                    etag = None
                    resource_exists = False
                    method = "PUT"
                    target_url = target_url_put
                    log_action = "Uploading (PUT)"  # Defaults for simple PUT

                    # --- Conditional Logic ---
                    if use_conditional:
                        yield json.dumps({"type": "progress", "message": f"{action_log_prefix}: Checking existence..."}) + "\n"
                        try:
                            get_response = session.get(target_url_put, headers=current_headers, timeout=15)
                            if get_response.status_code == 200:
                                resource_exists = True
                                etag = get_response.headers.get('ETag')
                                if etag:
                                    current_headers['If-Match'] = etag
                                    log_action = "Updating (conditional)"
                                    yield json.dumps({"type": "info", "message": f"  Resource exists. ETag: {etag}. Will use conditional PUT."}) + "\n"
                                else:
                                    log_action = "Updating (no ETag)"
                                    yield json.dumps({"type": "warning", "message": f"  Resource exists but no ETag found. Will use simple PUT."}) + "\n"
                                method = "PUT"
                                target_url = target_url_put
                            elif get_response.status_code == 404:
                                resource_exists = False
                                method = "PUT"
                                target_url = target_url_put  # Use PUT for creation with specific ID
                                log_action = "Creating (PUT)"
                                yield json.dumps({"type": "info", "message": f"  Resource not found. Will use PUT to create."}) + "\n"
                            else:
                                get_response.raise_for_status()
                        except requests.exceptions.HTTPError as http_err:
                            error_msg = f"Error checking existence for {full_id} (Status: {http_err.response.status_code}). Cannot proceed conditionally."
                            yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                            errors.append(f"{full_id}: {error_msg}")
                            error_count += 1
                            if error_handling_mode == 'stop':
                                raise ValueError("Stopping due to existence check error.")
                            continue
                        except requests.exceptions.RequestException as req_err:
                            error_msg = f"Network error checking existence for {full_id}: {req_err}. Cannot proceed conditionally."
                            yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                            errors.append(f"{full_id}: {error_msg}")
                            error_count += 1
                            if error_handling_mode == 'stop':
                                raise ValueError("Stopping due to existence check network error.")
                            continue

                    # --- Perform Upload Action ---
                    try:
                        yield json.dumps({"type": "progress", "message": f"{action_log_prefix}: {log_action}..."}) + "\n"
                        if method == "POST":
                            response = session.post(target_url, json=resource_to_upload, headers=current_headers, timeout=30)
                        else:
                            response = session.put(target_url, json=resource_to_upload, headers=current_headers, timeout=30)
                        response.raise_for_status()

                        status_code = response.status_code
                        success_msg = f"{log_action} successful for {full_id} (Status: {status_code})"
                        if method == "POST" and status_code == 201:
                            location = response.headers.get('Location')
                            success_msg += f" Loc: {location}" if location else ""
                        yield json.dumps({"type": "success", "message": success_msg}) + "\n"
                        resources_uploaded_count += 1

                    except requests.exceptions.HTTPError as e:
                        status_code = e.response.status_code if e.response is not None else 'N/A'
                        outcome_text = ""
                        if e.response is not None:
                            try:
                                outcome = e.response.json()
                                if outcome and outcome.get('resourceType') == 'OperationOutcome':
                                    issue_texts = []
                                    for issue in outcome.get('issue', []):
                                        severity = issue.get('severity', 'info')
                                        diag = issue.get('diagnostics') or issue.get('details', {}).get('text', 'No details')
                                        issue_texts.append(f"{severity}: {diag}")
                                    if issue_texts:
                                        outcome_text = "; ".join(issue_texts)
                                    else:
                                        outcome_text = e.response.text[:200]
                                else:
                                    outcome_text = e.response.text[:200]
                            except ValueError:
                                outcome_text = e.response.text[:200]
                        else:
                            outcome_text = "No response body."
                        error_prefix = "Conditional update failed" if status_code == 412 else f"{method} failed"
                        error_msg = f"{error_prefix} for {full_id} (Status: {status_code}): {outcome_text or str(e)}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(f"{full_id}: {error_msg}")
                        error_count += 1
                        if error_handling_mode == 'stop':
                            raise ValueError(f"Stopping due to {method} error.")
                    except requests.exceptions.Timeout:
                        error_msg = f"Timeout during {method} for {full_id}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(f"{full_id}: {error_msg}")
                        error_count += 1
                        if error_handling_mode == 'stop':
                            raise ValueError("Stopping due to upload timeout.")
                    except requests.exceptions.ConnectionError as e:
                        error_msg = f"Connection error during {method} for {full_id}: {e}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(f"{full_id}: {error_msg}")
                        error_count += 1
                        if error_handling_mode == 'stop':
                            raise ValueError("Stopping due to connection error.")
                    except requests.exceptions.RequestException as e:
                        error_msg = f"Request error during {method} for {full_id}: {str(e)}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(f"{full_id}: {error_msg}")
                        error_count += 1
                        if error_handling_mode == 'stop':
                            raise ValueError("Stopping due to request error.")
                    except Exception as e:
                        error_msg = f"Unexpected error during {method} for {full_id}: {str(e)}"
                        yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                        errors.append(f"{full_id}: {error_msg}")
                        error_count += 1
                        logger.error(f"Upload error for {full_id}", exc_info=True)
                        if error_handling_mode == 'stop':
                            raise ValueError("Stopping due to unexpected upload error.")

                yield json.dumps({"type": "info", "message": f"Individual upload loop finished."}) + "\n"

    except ValueError as ve:
        logger.error(f"Processing stopped: {ve}")
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        error_count += 1
        errors.append(f"Critical Error: {str(e)}")
        yield json.dumps({"type": "error", "message": f"Critical error: {str(e)}"}) + "\n"

    # --- Final Summary ---
    final_status = "unknown"
    total_errors = error_count + validation_errors_count
    if total_errors > 0:
        final_status = "failure" if resources_uploaded_count == 0 else "partial"
    elif resource_map or resources_parsed_list:
        final_status = "success"
    elif files_processed_count > 0:
        final_status = "success"
    else:
        final_status = "success"
    summary_message = f"Processing finished. Status: {final_status}. Files: {files_processed_count}, Parsed: {len(resources_parsed_list)}, Validation Errors: {validation_errors_count}, Validation Warnings: {validation_warnings_count}, Uploaded: {resources_uploaded_count}, Upload Errors: {error_count}."
    summary = {
        "status": final_status,
        "message": summary_message,
        "files_processed": files_processed_count,
        "resources_parsed": len(resources_parsed_list),
        "validation_errors": validation_errors_count,
        "validation_warnings": validation_warnings_count,
        "resources_uploaded": resources_uploaded_count,
        "error_count": error_count,
        "errors": errors
    }
    yield json.dumps({"type": "complete", "data": summary}) + "\n"
    logger.info(f"[Upload Test Data] Completed. Status: {final_status}. {summary_message}")

# --- END Service Function ---

# --- CORRECTED retrieve_bundles function with NEW logic ---
def retrieve_bundles(fhir_server_url, resources, output_zip, validate_references=False, fetch_reference_bundles=False, auth_type='none', auth_token=None):
    """
    Retrieve FHIR bundles and save to a ZIP file.
    Optionally fetches referenced resources, either individually by ID or as full bundles by type.
    Supports authentication for custom FHIR servers.
    Yields NDJSON progress updates.
    """
    temp_dir = None
    try:
        total_initial_bundles = 0
        fetched_individual_references = 0
        fetched_type_bundles = 0
        retrieved_references_or_types = set()

        temp_dir = tempfile.mkdtemp(prefix="fhir_retrieve_")
        logger.debug(f"Created temporary directory for bundle retrieval: {temp_dir}")
        yield json.dumps({"type": "progress", "message": f"Starting bundle retrieval for {len(resources)} resource types"}) + "\n"
        if validate_references:
            yield json.dumps({"type": "info", "message": f"Reference fetching ON (Mode: {'Full Type Bundles' if fetch_reference_bundles else 'Individual Resources'})"}) + "\n"
        else:
            yield json.dumps({"type": "info", "message": "Reference fetching OFF"}) + "\n"

        # Determine Base URL and Headers for Proxy
        base_proxy_url = f"{current_app.config['APP_BASE_URL'].rstrip('/')}/fhir"
        headers = {'Accept': 'application/fhir+json, application/fhir+xml;q=0.9, */*;q=0.8'}
        is_custom_url = fhir_server_url != '/fhir' and fhir_server_url is not None and fhir_server_url.startswith('http')
        if is_custom_url:
            headers['X-Target-FHIR-Server'] = fhir_server_url.rstrip('/')
            if auth_type in ['bearer', 'basic'] and auth_token:
                auth_display = 'Basic <redacted>' if auth_type == 'basic' else (auth_token[:10] + '...' if len(auth_token) > 10 else auth_token)
                yield json.dumps({"type": "info", "message": f"Using {auth_type} auth with header: Authorization: {auth_display}"}) + "\n"
                headers['Authorization'] = auth_token
            else:
                yield json.dumps({"type": "info", "message": "Using no authentication for custom URL"}) + "\n"
            logger.debug(f"Will use proxy with X-Target-FHIR-Server: {headers['X-Target-FHIR-Server']}")
        else:
            yield json.dumps({"type": "info", "message": "Using no authentication for local HAPI server"}) + "\n"
            logger.debug("Will use proxy targeting local HAPI server")

        # Fetch Initial Bundles
        initial_bundle_files = []
        for resource_type in resources:
            url = f"{base_proxy_url}/{quote(resource_type)}"
            yield json.dumps({"type": "progress", "message": f"Fetching bundle for {resource_type} via proxy..."}) + "\n"
            logger.debug(f"Sending GET request to proxy {url} with headers: {json.dumps(headers)}")
            try:
                response = requests.get(url, headers=headers, timeout=60)
                logger.debug(f"Proxy response for {resource_type}: HTTP {response.status_code}")
                if response.status_code != 200:
                    error_detail = f"Proxy returned HTTP {response.status_code}."
                    try: error_detail += f" Body: {response.text[:200]}..."
                    except: pass
                    yield json.dumps({"type": "error", "message": f"Failed to fetch {resource_type}: {error_detail}"}) + "\n"
                    logger.error(f"Failed to fetch {resource_type} via proxy {url}: {error_detail}")
                    continue
                try:
                    bundle = response.json()
                except ValueError as e:
                    yield json.dumps({"type": "error", "message": f"Invalid JSON response for {resource_type}: {str(e)}"}) + "\n"
                    logger.error(f"Invalid JSON from proxy for {resource_type} at {url}: {e}, Response: {response.text[:500]}")
                    continue
                if not isinstance(bundle, dict) or bundle.get('resourceType') != 'Bundle':
                    yield json.dumps({"type": "error", "message": f"Expected Bundle for {resource_type}, got {bundle.get('resourceType', 'unknown')}"}) + "\n"
                    logger.error(f"Expected Bundle for {resource_type}, got {bundle.get('resourceType', 'unknown')}")
                    continue
                if not bundle.get('entry'):
                    yield json.dumps({"type": "warning", "message": f"No entries found in bundle for {resource_type}"}) + "\n"

                # Save the bundle
                output_file = os.path.join(temp_dir, f"{resource_type}_bundle.json")
                try:
                    with open(output_file, 'w', encoding='utf-8') as f:
                        json.dump(bundle, f, indent=2)
                    logger.debug(f"Wrote bundle to {output_file}")
                    initial_bundle_files.append(output_file)
                    total_initial_bundles += 1
                    yield json.dumps({"type": "success", "message": f"Saved bundle for {resource_type}"}) + "\n"
                except IOError as e:
                    yield json.dumps({"type": "error", "message": f"Failed to save bundle file for {resource_type}: {e}"}) + "\n"
                    logger.error(f"Failed to write bundle file {output_file}: {e}")
                    continue
            except requests.RequestException as e:
                yield json.dumps({"type": "error", "message": f"Error connecting to proxy for {resource_type}: {str(e)}"}) + "\n"
                logger.error(f"Error retrieving bundle for {resource_type} via proxy {url}: {e}")
                continue
            except Exception as e:
                yield json.dumps({"type": "error", "message": f"Unexpected error fetching {resource_type}: {str(e)}"}) + "\n"
                logger.error(f"Unexpected error during initial fetch for {resource_type} at {url}: {e}", exc_info=True)
                continue

        # Fetch Referenced Resources (Conditionally)
        if validate_references and initial_bundle_files:
            yield json.dumps({"type": "progress", "message": "Scanning retrieved bundles for references..."}) + "\n"
            all_references = set()
            references_by_type = defaultdict(set)

            # Scan for References
            for bundle_file_path in initial_bundle_files:
                try:
                    with open(bundle_file_path, 'r', encoding='utf-8') as f:
                        bundle = json.load(f)
                    for entry in bundle.get('entry', []):
                        resource = entry.get('resource')
                        if resource:
                            current_refs = []
                            find_references(resource, current_refs)
                            for ref_str in current_refs:
                                if isinstance(ref_str, str) and '/' in ref_str and not ref_str.startswith('#'):
                                    all_references.add(ref_str)
                                    try:
                                        ref_type = ref_str.split('/')[0]
                                        if ref_type:
                                            references_by_type[ref_type].add(ref_str)
                                    except Exception:
                                        pass
                except Exception as e:
                    yield json.dumps({"type": "warning", "message": f"Could not scan references in {os.path.basename(bundle_file_path)}: {e}"}) + "\n"
                    logger.warning(f"Error processing references in {bundle_file_path}: {e}")

            # Fetch Logic
            if not all_references:
                yield json.dumps({"type": "info", "message": "No references found to fetch."}) + "\n"
            else:
                if fetch_reference_bundles:
                    # Fetch Full Bundles by Type
                    unique_ref_types = sorted(list(references_by_type.keys()))
                    yield json.dumps({"type": "progress", "message": f"Fetching full bundles for {len(unique_ref_types)} referenced types..."}) + "\n"
                    logger.info(f"Fetching full bundles for referenced types: {unique_ref_types}")

                    for ref_type in unique_ref_types:
                        if ref_type in retrieved_references_or_types:
                            continue

                        url = f"{base_proxy_url}/{quote(ref_type)}"
                        yield json.dumps({"type": "progress", "message": f"Fetching full bundle for type {ref_type} via proxy..."}) + "\n"
                        logger.debug(f"Sending GET request for full type bundle {ref_type} to proxy {url} with headers: {json.dumps(headers)}")
                        try:
                            response = requests.get(url, headers=headers, timeout=180)
                            logger.debug(f"Proxy response for {ref_type} bundle: HTTP {response.status_code}")
                            if response.status_code != 200:
                                error_detail = f"Proxy returned HTTP {response.status_code}."
                                try: error_detail += f" Body: {response.text[:200]}..."
                                except: pass
                                yield json.dumps({"type": "warning", "message": f"Failed to fetch full bundle for {ref_type}: {error_detail}"}) + "\n"
                                logger.warning(f"Failed to fetch full bundle {ref_type} via proxy {url}: {error_detail}")
                                retrieved_references_or_types.add(ref_type)
                                continue

                            try:
                                bundle = response.json()
                            except ValueError as e:
                                yield json.dumps({"type": "warning", "message": f"Invalid JSON for full {ref_type} bundle: {str(e)}"}) + "\n"
                                logger.warning(f"Invalid JSON response from proxy for full {ref_type} bundle at {url}: {e}")
                                retrieved_references_or_types.add(ref_type)
                                continue

                            if not isinstance(bundle, dict) or bundle.get('resourceType') != 'Bundle':
                                yield json.dumps({"type": "warning", "message": f"Expected Bundle for full {ref_type} fetch, got {bundle.get('resourceType', 'unknown')}"}) + "\n"
                                logger.warning(f"Expected Bundle for full {ref_type} fetch, got {bundle.get('resourceType', 'unknown')}")
                                retrieved_references_or_types.add(ref_type)
                                continue

                            # Save the full type bundle
                            output_file = os.path.join(temp_dir, f"ref_{ref_type}_BUNDLE.json")
                            try:
                                with open(output_file, 'w', encoding='utf-8') as f:
                                    json.dump(bundle, f, indent=2)
                                logger.debug(f"Wrote full type bundle to {output_file}")
                                fetched_type_bundles += 1
                                retrieved_references_or_types.add(ref_type)
                                yield json.dumps({"type": "success", "message": f"Saved full bundle for type {ref_type}"}) + "\n"
                            except IOError as e:
                                yield json.dumps({"type": "warning", "message": f"Failed to save full bundle file for {ref_type}: {e}"}) + "\n"
                                logger.error(f"Failed to write full bundle file {output_file}: {e}")
                                retrieved_references_or_types.add(ref_type)
                        except requests.RequestException as e:
                            yield json.dumps({"type": "warning", "message": f"Error connecting to proxy for full {ref_type} bundle: {str(e)}"}) + "\n"
                            logger.warning(f"Error retrieving full {ref_type} bundle via proxy: {e}")
                            retrieved_references_or_types.add(ref_type)
                        except Exception as e:
                            yield json.dumps({"type": "warning", "message": f"Unexpected error fetching full {ref_type} bundle: {str(e)}"}) + "\n"
                            logger.warning(f"Unexpected error during full {ref_type} bundle fetch: {e}", exc_info=True)
                            retrieved_references_or_types.add(ref_type)
                else:
                    # Fetch Individual Referenced Resources
                    yield json.dumps({"type": "progress", "message": f"Fetching {len(all_references)} unique referenced resources individually..."}) + "\n"
                    logger.info(f"Fetching {len(all_references)} unique referenced resources by ID.")
                    for ref in sorted(list(all_references)):
                        if ref in retrieved_references_or_types:
                            continue

                        try:
                            ref_parts = ref.split('/')
                            if len(ref_parts) != 2 or not ref_parts[0] or not ref_parts[1]:
                                logger.warning(f"Skipping invalid reference format: {ref}")
                                continue
                            ref_type, ref_id = ref_parts

                            search_param = quote(f"_id={ref_id}")
                            url = f"{base_proxy_url}/{quote(ref_type)}?{search_param}"
                            yield json.dumps({"type": "progress", "message": f"Fetching referenced {ref_type}/{ref_id} via proxy..."}) + "\n"
                            logger.debug(f"Sending GET request for referenced {ref} to proxy {url} with headers: {json.dumps(headers)}")

                            response = requests.get(url, headers=headers, timeout=60)
                            logger.debug(f"Proxy response for referenced {ref}: HTTP {response.status_code}")

                            if response.status_code != 200:
                                error_detail = f"Proxy returned HTTP {response.status_code}."
                                try: error_detail += f" Body: {response.text[:200]}..."
                                except: pass
                                yield json.dumps({"type": "warning", "message": f"Failed to fetch referenced {ref}: {error_detail}"}) + "\n"
                                logger.warning(f"Failed to fetch referenced {ref} via proxy {url}: {error_detail}")
                                retrieved_references_or_types.add(ref)
                                continue

                            try:
                                bundle = response.json()
                            except ValueError as e:
                                yield json.dumps({"type": "warning", "message": f"Invalid JSON for referenced {ref}: {str(e)}"}) + "\n"
                                logger.warning(f"Invalid JSON from proxy for ref {ref} at {url}: {e}")
                                retrieved_references_or_types.add(ref)
                                continue

                            if not isinstance(bundle, dict) or bundle.get('resourceType') != 'Bundle':
                                yield json.dumps({"type": "warning", "message": f"Expected Bundle for referenced {ref}, got {bundle.get('resourceType', 'unknown')}"}) + "\n"
                                retrieved_references_or_types.add(ref)
                                continue

                            if not bundle.get('entry'):
                                yield json.dumps({"type": "info", "message": f"Referenced resource {ref} not found on server."}) + "\n"
                                logger.info(f"Referenced resource {ref} not found via search {url}")
                                retrieved_references_or_types.add(ref)
                                continue

                            # Save the bundle containing the single referenced resource
                            output_file = os.path.join(temp_dir, f"ref_{ref_type}_{ref_id}.json")
                            try:
                                with open(output_file, 'w', encoding='utf-8') as f:
                                    json.dump(bundle, f, indent=2)
                                logger.debug(f"Wrote referenced resource bundle to {output_file}")
                                fetched_individual_references += 1
                                retrieved_references_or_types.add(ref)
                                yield json.dumps({"type": "success", "message": f"Saved referenced resource {ref}"}) + "\n"
                            except IOError as e:
                                yield json.dumps({"type": "warning", "message": f"Failed to save file for referenced {ref}: {e}"}) + "\n"
                                logger.error(f"Failed to write file {output_file}: {e}")
                                retrieved_references_or_types.add(ref)
                        except requests.RequestException as e:
                            yield json.dumps({"type": "warning", "message": f"Network error fetching referenced {ref}: {str(e)}"}) + "\n"
                            logger.warning(f"Network error retrieving referenced {ref} via proxy: {e}")
                            retrieved_references_or_types.add(ref)
                        except Exception as e:
                            yield json.dumps({"type": "warning", "message": f"Unexpected error fetching referenced {ref}: {str(e)}"}) + "\n"
                            logger.warning(f"Unexpected error during reference fetch for {ref}: {e}", exc_info=True)
                            retrieved_references_or_types.add(ref)

        # Create Final ZIP File
        yield json.dumps({"type": "progress", "message": f"Creating ZIP file {os.path.basename(output_zip)}..."}) + "\n"
        files_to_zip = [f for f in os.listdir(temp_dir) if f.endswith('.json')]
        if not files_to_zip:
            yield json.dumps({"type": "warning", "message": "No bundle files were successfully retrieved to include in ZIP."}) + "\n"
            logger.warning(f"No JSON files found in {temp_dir} to include in ZIP.")
        else:
            logger.debug(f"Found {len(files_to_zip)} JSON files to include in ZIP: {files_to_zip}")
            try:
                with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for filename in files_to_zip:
                        file_path = os.path.join(temp_dir, filename)
                        if os.path.exists(file_path):
                            zipf.write(file_path, filename)
                        else:
                            logger.error(f"File {file_path} disappeared before adding to ZIP.")
                yield json.dumps({"type": "success", "message": f"ZIP file created: {os.path.basename(output_zip)} with {len(files_to_zip)} files."}) + "\n"
            except Exception as e:
                yield json.dumps({"type": "error", "message": f"Failed to create ZIP file: {e}"}) + "\n"
                logger.error(f"Error creating ZIP file {output_zip}: {e}", exc_info=True)

        # Final Completion Message
        completion_message = (
            f"Bundle retrieval finished. Initial bundles: {total_initial_bundles}, "
            f"Referenced items fetched: {fetched_individual_references if not fetch_reference_bundles else fetched_type_bundles} "
            f"({'individual resources' if not fetch_reference_bundles else 'full type bundles'})"
        )
        yield json.dumps({
            "type": "complete",
            "message": completion_message,
            "data": {
                "total_initial_bundles": total_initial_bundles,
                "fetched_individual_references": fetched_individual_references,
                "fetched_type_bundles": fetched_type_bundles,
                "reference_mode": "individual" if validate_references and not fetch_reference_bundles else "type_bundle" if validate_references and fetch_reference_bundles else "off"
            }
        }) + "\n"

    except Exception as e:
        yield json.dumps({"type": "error", "message": f"Critical error during retrieval setup: {str(e)}"}) + "\n"
        logger.error(f"Unexpected error in retrieve_bundles setup: {e}", exc_info=True)
        yield json.dumps({"type": "complete", "message": f"Retrieval failed: {str(e)}", "data": {"total_initial_bundles": 0, "fetched_individual_references": 0, "fetched_type_bundles": 0}}) + "\n"
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"Successfully removed temporary directory: {temp_dir}")
            except Exception as cleanup_e:
                logger.error(f"Error removing temporary directory {temp_dir}: {cleanup_e}", exc_info=True)
# --- End corrected retrieve_bundles function ---

def split_bundles(input_zip_path, output_zip):
    """Split FHIR bundles from a ZIP file into individual resource JSON files and save to a ZIP."""
    try:
        total_resources = 0
        temp_dir = tempfile.mkdtemp()
        yield json.dumps({"type": "progress", "message": f"Starting bundle splitting from ZIP"}) + "\n"

        # Extract input ZIP
        with zipfile.ZipFile(input_zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        yield json.dumps({"type": "progress", "message": f"Extracted input ZIP to temporary directory"}) + "\n"

        # Process JSON files
        for filename in os.listdir(temp_dir):
            if not filename.endswith('.json'):
                continue
            input_file = os.path.join(temp_dir, filename)
            try:
                with open(input_file, 'r', encoding='utf-8') as f:
                    bundle = json.load(f)
                if bundle.get('resourceType') != 'Bundle':
                    yield json.dumps({"type": "error", "message": f"Skipping {filename}: Not a Bundle"}) + "\n"
                    continue
                yield json.dumps({"type": "progress", "message": f"Processing bundle {filename}"}) + "\n"
                index = 1
                for entry in bundle.get('entry', []):
                    resource = entry.get('resource')
                    if not resource or not resource.get('resourceType'):
                        yield json.dumps({"type": "error", "message": f"Invalid resource in {filename} at entry {index}"}) + "\n"
                        continue
                    resource_type = resource['resourceType']
                    output_file = os.path.join(temp_dir, f"{resource_type}-{index}.json")
                    with open(output_file, 'w', encoding='utf-8') as f:
                        json.dump(resource, f, indent=2)
                    total_resources += 1
                    yield json.dumps({"type": "success", "message": f"Saved {resource_type}-{index}.json"}) + "\n"
                    index += 1
            except Exception as e:
                yield json.dumps({"type": "error", "message": f"Error processing {filename}: {str(e)}"}) + "\n"
                logger.error(f"Error splitting bundle {filename}: {e}", exc_info=True)

        # Create output ZIP
        with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for filename in os.listdir(temp_dir):
                if filename.endswith('.json') and '-' in filename:
                    zipf.write(os.path.join(temp_dir, filename), filename)
        yield json.dumps({
            "type": "complete",
            "message": f"Bundle splitting completed. Extracted {total_resources} resources.",
            "data": {"total_resources": total_resources}
        }) + "\n"
    except Exception as e:
        yield json.dumps({"type": "error", "message": f"Unexpected error during splitting: {str(e)}"}) + "\n"
        logger.error(f"Unexpected error in split_bundles: {e}", exc_info=True)
    finally:
        if os.path.exists(temp_dir):
            for filename in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, filename))
            os.rmdir(temp_dir)


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

