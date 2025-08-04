# ------------------------------------------------------------------------------
# Dockerfile for FHIRFLARE-IG-Toolkit (Optimized for Python/Flask)
#
# This Dockerfile builds a container for the FHIRFLARE-IG-Toolkit application.
#
# Key Features:
# - Uses python:3.11-slim as the base image for a minimal, secure Python runtime.
# - Installs Node.js and global NPM packages (gofsh, fsh-sushi) for FHIR IG tooling.
# - Sets up a Python virtual environment and installs all Python dependencies.
# - Installs and configures Supervisor to manage the Flask app and related processes.
# - Copies all necessary application code, templates, static files, and configuration.
# - Exposes ports 5000 (Flask) and 8080 (optional, for compatibility).
# - Entrypoint runs Supervisor for process management.
#
# Notes:
# - The Dockerfile is optimized for Python. Tomcat/Java is not included.
# - Node.js is only installed if needed for FHIR IG tooling.
# - The image is suitable for development and production with minimal changes.
# ------------------------------------------------------------------------------

# Optimized Dockerfile for Python (Flask)
FROM python:3.11-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

# Optional: Install Node.js if needed for GoFSH/SUSHI
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g gofsh fsh-sushi \
    && rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN python -m venv /app/venv \
    && . /app/venv/bin/activate \
    && pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y fhirpath || true \
    && pip install --no-cache-dir fhirpathpy \
    && pip install supervisor

# Copy application files
COPY app.py .
COPY services.py .
COPY forms.py .
COPY package.py .
COPY templates/ templates/
COPY static/ static/
COPY tests/ tests/
COPY supervisord.conf /etc/supervisord.conf

# Expose ports
EXPOSE 5000 8080

# Set environment
ENV PATH="/app/venv/bin:$PATH"

# Start supervisord
CMD ["supervisord", "-c", "/etc/supervisord.conf"]