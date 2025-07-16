from flask import Blueprint, jsonify, current_app, render_template
import os
import tarfile
import json
from datetime import datetime
import time
from services import pkg_version, safe_parse_version

package_bp = Blueprint('package', __name__)

@package_bp.route('/logs/<name>')
def logs(name):
    """
    Fetch logs for a package, listing each version with its publication date.
    
    Args:
        name (str): The name of the package.
    
    Returns:
        Rendered template with logs or an error message.
    """
    try:
        in_memory_cache = current_app.config.get('MANUAL_PACKAGE_CACHE', [])
        if not in_memory_cache:
            current_app.logger.error(f"No in-memory cache found for package logs: {name}")
            return "<p class='text-muted'>Package cache not found.</p>"

        package_data = next((pkg for pkg in in_memory_cache if isinstance(pkg, dict) and pkg.get('name', '').lower() == name.lower()), None)
        if not package_data:
            current_app.logger.error(f"Package not found in cache: {name}")
            return "<p class='text-muted'>Package not found.</p>"

        # Get the versions list with pubDate
        versions = package_data.get('all_versions', [])
        if not versions:
            current_app.logger.warning(f"No versions found for package: {name}. Package data: {package_data}")
            return "<p class='text-muted'>No version history found for this package.</p>"

        current_app.logger.debug(f"Found {len(versions)} versions for package {name}: {versions[:5]}...")

        logs = []
        now = time.time()
        for version_info in versions:
            if not isinstance(version_info, dict):
                current_app.logger.warning(f"Invalid version info for {name}: {version_info}")
                continue
            version = version_info.get('version', '')
            pub_date_str = version_info.get('pubDate', '')
            if not version or not pub_date_str:
                current_app.logger.warning(f"Skipping version info with missing version or pubDate: {version_info}")
                continue

            # Parse pubDate and calculate "when"
            when = "Unknown"
            try:
                pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
                pub_time = pub_date.timestamp()
                time_diff = now - pub_time
                days_ago = int(time_diff / 86400)
                if days_ago < 1:
                    hours_ago = int(time_diff / 3600)
                    if hours_ago < 1:
                        minutes_ago = int(time_diff / 60)
                        when = f"{minutes_ago} minute{'s' if minutes_ago != 1 else ''} ago"
                    else:
                        when = f"{hours_ago} hour{'s' if hours_ago != 1 else ''} ago"
                else:
                    when = f"{days_ago} day{'s' if days_ago != 1 else ''} ago"
            except ValueError as e:
                current_app.logger.warning(f"Failed to parse pubDate '{pub_date_str}' for version {version}: {e}")

            logs.append({
                "version": version,
                "pubDate": pub_date_str,
                "when": when
            })

        if not logs:
            current_app.logger.warning(f"No valid version entries with pubDate for package: {name}")
            return "<p class='text-muted'>No version history found for this package.</p>"

        # Sort logs by version number (newest first)
        logs.sort(key=lambda x: safe_parse_version(x.get('version', '0.0.0a0')), reverse=True)

        current_app.logger.debug(f"Rendering logs for {name} with {len(logs)} entries")
        return render_template('package.logs.html', logs=logs)

    except Exception as e:
        current_app.logger.error(f"Error in logs endpoint for {name}: {str(e)}", exc_info=True)
        return "<p class='text-danger'>Error loading version history.</p>", 500

@package_bp.route('/dependents/<name>')
def dependents(name):
    """
    HTMX endpoint to fetch packages that depend on the current package.
    Returns an HTML fragment with a table of dependent packages.
    """
    in_memory_cache = current_app.config.get('MANUAL_PACKAGE_CACHE', [])
    package_data = next((pkg for pkg in in_memory_cache if isinstance(pkg, dict) and pkg.get('name', '').lower() == name.lower()), None)

    if not package_data:
        return "<p class='text-danger'>Package not found.</p>"

    # Find dependents: packages whose dependencies include the current package
    dependents = []
    for pkg in in_memory_cache:
        if not isinstance(pkg, dict):
            continue
        dependencies = pkg.get('dependencies', [])
        for dep in dependencies:
            dep_name = dep.get('name', '')
            if dep_name.lower() == name.lower():
                dependents.append({
                    "name": pkg.get('name', 'Unknown'),
                    "version": pkg.get('latest_absolute_version', 'N/A'),
                    "author": pkg.get('author', 'N/A'),
                    "fhir_version": pkg.get('fhir_version', 'N/A'),
                    "version_count": pkg.get('version_count', 0),
                    "canonical": pkg.get('canonical', 'N/A')
                })
                break

    return render_template('package.dependents.html', dependents=dependents)