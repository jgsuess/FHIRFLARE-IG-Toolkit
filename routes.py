# app/modules/fhir_ig_importer/routes.py

import requests
import os
import tarfile # Needed for find_and_extract_sd
import gzip
import json
import io
import re
from flask import (render_template, redirect, url_for, flash, request,
                   current_app, jsonify, send_file)
from flask_login import login_required
from app.decorators import admin_required
from werkzeug.utils import secure_filename
from . import bp
from .forms import IgImportForm
# Import the services module
from . import services
# Import ProcessedIg model for get_structure_definition
from app.models import ProcessedIg
from app import db


# --- Helper: Find/Extract SD ---
# Moved from services.py to be local to routes that use it, or keep in services and call services.find_and_extract_sd
def find_and_extract_sd(tgz_path, resource_identifier):
    """Helper to find and extract SD json from a given tgz path by ID, Name, or Type."""
    sd_data = None; found_path = None; logger = current_app.logger # Use current_app logger
    if not tgz_path or not os.path.exists(tgz_path): logger.error(f"File not found in find_and_extract_sd: {tgz_path}"); return None, None
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            logger.debug(f"Searching for SD matching '{resource_identifier}' in {os.path.basename(tgz_path)}")
            for member in tar:
                if member.isfile() and member.name.startswith('package/') and member.name.lower().endswith('.json'):
                    if os.path.basename(member.name).lower() in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']: continue
                    fileobj = None
                    try:
                        fileobj = tar.extractfile(member)
                        if fileobj:
                            content_bytes = fileobj.read(); content_string = content_bytes.decode('utf-8-sig'); data = json.loads(content_string)
                            if isinstance(data, dict) and data.get('resourceType') == 'StructureDefinition':
                                sd_id = data.get('id'); sd_name = data.get('name'); sd_type = data.get('type')
                                if resource_identifier == sd_type or resource_identifier == sd_id or resource_identifier == sd_name:
                                     sd_data = data; found_path = member.name; logger.info(f"Found matching SD for '{resource_identifier}' at path: {found_path}"); break
                    except Exception as e: logger.warning(f"Could not read/parse potential SD {member.name}: {e}")
                    finally:
                        if fileobj: fileobj.close()
            if sd_data is None: logger.warning(f"SD matching '{resource_identifier}' not found within archive {os.path.basename(tgz_path)}")
    except Exception as e: logger.error(f"Error reading archive {tgz_path} in find_and_extract_sd: {e}", exc_info=True); raise
    return sd_data, found_path
# --- End Helper ---


# --- Route for the main import page ---
@bp.route('/import-ig', methods=['GET', 'POST'])
@login_required
@admin_required
def import_ig():
    """Handles FHIR IG recursive download using services."""
    form = IgImportForm()
    template_context = {"title": "Import FHIR IG", "form": form, "results": None }
    if form.validate_on_submit():
        package_name = form.package_name.data; package_version = form.package_version.data
        template_context.update(package_name=package_name, package_version=package_version)
        flash(f"Starting full import for {package_name}#{package_version}...", "info"); current_app.logger.info(f"Calling import service for: {package_name}#{package_version}")
        try:
            # Call the CORRECT orchestrator service function
            import_results = services.import_package_and_dependencies(package_name, package_version)
            template_context["results"] = import_results
            # Flash summary messages
            dl_count = len(import_results.get('downloaded', {})); proc_count = len(import_results.get('processed', set())); error_count = len(import_results.get('errors', []))
            if dl_count > 0: flash(f"Downloaded/verified {dl_count} package file(s).", "success")
            if proc_count < dl_count and dl_count > 0 : flash(f"Dependency data extraction failed for {dl_count - proc_count} package(s).", "warning")
            if error_count > 0: flash(f"{error_count} total error(s) occurred.", "danger")
            elif dl_count == 0 and error_count == 0: flash("No packages needed downloading or initial package failed.", "info")
            elif error_count == 0: flash("Import process completed successfully.", "success")
        except Exception as e:
             fatal_error = f"Critical unexpected error during import: {e}"; template_context["fatal_error"] = fatal_error; current_app.logger.error(f"Critical import error: {e}", exc_info=True); flash(fatal_error, "danger")
        return render_template('fhir_ig_importer/import_ig_page.html', **template_context)
    return render_template('fhir_ig_importer/import_ig_page.html', **template_context)


# --- Route to get StructureDefinition elements ---
@bp.route('/get-structure')
@login_required
@admin_required
def get_structure_definition():
    """API endpoint to fetch SD elements and pre-calculated Must Support paths."""
    package_name = request.args.get('package_name'); package_version = request.args.get('package_version'); resource_identifier = request.args.get('resource_type')
    error_response_data = {"elements": [], "must_support_paths": []}
    if not all([package_name, package_version, resource_identifier]): error_response_data["error"] = "Missing query parameters"; return jsonify(error_response_data), 400
    current_app.logger.info(f"Request for structure: {package_name}#{package_version} / {resource_identifier}")

    # Find the primary package file
    package_dir_name = 'fhir_packages'; download_dir = os.path.join(current_app.instance_path, package_dir_name)
    # Use service helper for consistency
    filename = services._construct_tgz_filename(package_name, package_version)
    tgz_path = os.path.join(download_dir, filename)
    if not os.path.exists(tgz_path): error_response_data["error"] = f"Package file not found: {filename}"; return jsonify(error_response_data), 404

    sd_data = None; found_path = None; error_msg = None
    try:
        # Call the local helper function correctly
        sd_data, found_path = find_and_extract_sd(tgz_path, resource_identifier)
        # Fallback check
        if sd_data is None:
            core_pkg_name = "hl7.fhir.r4.core"; core_pkg_version = "4.0.1" # TODO: Make dynamic
            core_filename = services._construct_tgz_filename(core_pkg_name, core_pkg_version)
            core_tgz_path = os.path.join(download_dir, core_filename)
            if os.path.exists(core_tgz_path):
                 current_app.logger.info(f"Trying fallback search in {core_pkg_name}...")
                 sd_data, found_path = find_and_extract_sd(core_tgz_path, resource_identifier) # Call local helper
            else: current_app.logger.warning(f"Core package {core_tgz_path} not found.")
    except Exception as e:
         error_msg = f"Error searching package(s): {e}"; current_app.logger.error(error_msg, exc_info=True); error_response_data["error"] = error_msg; return jsonify(error_response_data), 500

    if sd_data is None: error_msg = f"SD for '{resource_identifier}' not found."; error_response_data["error"] = error_msg; return jsonify(error_response_data), 404

    # Extract elements
    elements = sd_data.get('snapshot', {}).get('element', [])
    if not elements: elements = sd_data.get('differential', {}).get('element', [])

    # Fetch pre-calculated Must Support paths from DB
    must_support_paths = [];
    try:
        stmt = db.select(ProcessedIg).filter_by(package_name=package_name, package_version=package_version); processed_ig_record = db.session.scalar(stmt)
        if processed_ig_record: all_ms_paths_dict = processed_ig_record.must_support_elements; must_support_paths = all_ms_paths_dict.get(resource_identifier, [])
        else: current_app.logger.warning(f"No ProcessedIg record found for {package_name}#{package_version}")
    except Exception as e: current_app.logger.error(f"Error fetching MS paths from DB: {e}", exc_info=True)

    current_app.logger.info(f"Returning {len(elements)} elements for {resource_identifier} from {found_path or 'Unknown File'}")
    return jsonify({"elements": elements, "must_support_paths": must_support_paths})


# --- Route to get raw example file content ---
@bp.route('/get-example')
@login_required
@admin_required
def get_example_content():
    # ... (Function remains the same as response #147) ...
    package_name = request.args.get('package_name'); package_version = request.args.get('package_version'); example_member_path = request.args.get('filename')
    if not all([package_name, package_version, example_member_path]): return jsonify({"error": "Missing query parameters"}), 400
    current_app.logger.info(f"Request for example: {package_name}#{package_version} / {example_member_path}")
    package_dir_name = 'fhir_packages'; download_dir = os.path.join(current_app.instance_path, package_dir_name)
    pkg_filename = services._construct_tgz_filename(package_name, package_version) # Use service helper
    tgz_path = os.path.join(download_dir, pkg_filename)
    if not os.path.exists(tgz_path): return jsonify({"error": f"Package file not found: {pkg_filename}"}), 404
    # Basic security check on member path
    safe_member_path = secure_filename(example_member_path.replace("package/","")) # Allow paths within package/
    if not example_member_path.startswith('package/') or '..' in example_member_path: return jsonify({"error": "Invalid example file path."}), 400

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            try: example_member = tar.getmember(example_member_path) # Use original path here
            except KeyError: return jsonify({"error": f"Example file '{example_member_path}' not found."}), 404
            example_fileobj = tar.extractfile(example_member)
            if not example_fileobj: return jsonify({"error": "Could not extract example file."}), 500
            try: content_bytes = example_fileobj.read()
            finally: example_fileobj.close()
            return content_bytes # Return raw bytes
    except tarfile.TarError as e: err_msg = f"Error reading {tgz_path}: {e}"; current_app.logger.error(err_msg); return jsonify({"error": err_msg}), 500
    except Exception as e: err_msg = f"Unexpected error getting example {example_member_path}: {e}"; current_app.logger.error(err_msg, exc_info=True); return jsonify({"error": err_msg}), 500