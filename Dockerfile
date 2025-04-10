FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies (if any needed by services.py, e.g., for FHIR processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY services.py .  # Assuming you have this; replace with actual file if different
COPY instance/ instance/  # Pre-create instance dir if needed for SQLite/packages

# Ensure instance directory exists for SQLite DB and FHIR packages
RUN mkdir -p /app/instance/fhir_packages

# Expose Flask port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=app.py
ENV FLASK_ENV=development

# Run the app
CMD ["flask", "run", "--host=0.0.0.0"]