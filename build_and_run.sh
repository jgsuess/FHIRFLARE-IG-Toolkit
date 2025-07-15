#!/bin/bash
set -e

# --- Configuration ---
REPO_URL="https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git"
CLONE_DIR="hapi-fhir-jpaserver"
SOURCE_CONFIG_DIR="hapi-fhir-setup"
CONFIG_FILE="application.yaml"

# --- Define Paths ---
SOURCE_CONFIG_PATH="../$SOURCE_CONFIG_DIR/target/classes/$CONFIG_FILE"
DEST_CONFIG_PATH="$CLONE_DIR/target/classes/$CONFIG_FILE"

# === Prompt for Version ===
while true; do
    echo "Select Installation Mode:"
    echo "1. Standalone (Includes local HAPI FHIR Server - Requires Git & Maven)"
    echo "2. Lite (Excludes local HAPI FHIR Server - No Git/Maven needed)"
    read -p "Enter your choice (1 or 2): " choice
    case $choice in
        1)
            APP_MODE="standalone"
            break
            ;;
        2)
            APP_MODE="lite"
            break
            ;;
        *)
            echo "Invalid input. Please try again."
            ;;
    esac
done

echo "Selected Mode: $APP_MODE"
echo

# === Conditionally Execute HAPI Setup ===
if [ "$APP_MODE" = "standalone" ]; then
    echo "Running Standalone setup including HAPI FHIR..."
    echo

    # Step 0: Clean up previous clone (optional)
    if [ -d "$CLONE_DIR" ]; then
        echo "Found existing directory, removing it..."
        rm -rf "$CLONE_DIR"
        echo "Existing directory removed."
    else
        echo "Directory does not exist, proceeding with clone."
    fi
    echo

    # Step 1: Clone the HAPI FHIR server repository
    echo "Cloning repository: $REPO_URL into $CLONE_DIR..."
    git clone "$REPO_URL" "$CLONE_DIR"
    echo "Repository cloned successfully."
    echo

    # Step 2: Navigate into the cloned directory
    cd "$CLONE_DIR"
    echo "Current directory: $(pwd)"
    echo

    # Step 3: Build the HAPI server using Maven
    echo "===> Starting Maven build (Step 3)..."
    mvn clean package -DskipTests=true -Pboot
    echo "Maven build completed successfully."
    echo

    # Step 4: Copy the configuration file
    echo "===> Starting file copy (Step 4)..."
    echo "Copying configuration file..."
    echo "Source: $SOURCE_CONFIG_PATH"
    echo "Destination: target/classes/$CONFIG_FILE"
    cp "$SOURCE_CONFIG_PATH" "target/classes/" || echo "WARNING: Failed to copy configuration file. The server might use default configuration."
    echo "Configuration file copy step finished."
    echo

    # Step 5: Navigate back to the parent directory
    cd ..
    echo "Current directory: $(pwd)"
    echo
else
    echo "Running Lite setup, skipping HAPI FHIR build..."
    # Ensure the hapi-fhir-jpaserver directory doesn't exist or is empty if Lite mode is chosen after a standalone attempt
    if [ -d "$CLONE_DIR" ]; then
        echo "Found existing HAPI directory in Lite mode. Removing it to avoid build issues..."
        rm -rf "$CLONE_DIR"
    fi
    # Create empty target directories expected by Dockerfile COPY, even if not used
    mkdir -p "$CLONE_DIR/target/classes"
    mkdir -p "$CLONE_DIR/custom"
    # Create a placeholder empty WAR file to satisfy Dockerfile COPY
    touch "$CLONE_DIR/target/ROOT.war"
    touch "$CLONE_DIR/target/classes/application.yaml"
    echo "Placeholder files created for Lite mode build."
    echo
fi

# === Modify docker-compose.yml to set APP_MODE ===
echo "Updating docker-compose.yml with APP_MODE=$APP_MODE..."
cat <<EOF > docker-compose.yml.tmp
version: '3.8'
services:
  fhirflare:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "5000:5000"
      - "8080:8080"
    volumes:
      - ./instance:/app/instance
      - ./static/uploads:/app/static/uploads
      - ./instance/hapi-h2-data/:/app/h2-data
      - ./logs:/app/logs
    environment:
      - FLASK_APP=app.py
      - FLASK_ENV=development
      - NODE_PATH=/usr/lib/node_modules
      - APP_MODE=$APP_MODE
      - APP_BASE_URL=http://localhost:5000
      - HAPI_FHIR_URL=http://localhost:8080/fhir
    command: supervisord -c /etc/supervisord.conf
EOF

# Replace the original docker-compose.yml
mv docker-compose.yml.tmp docker-compose.yml

echo "docker-compose.yml updated successfully."
echo

# --- Step 6: Build Docker images ---
echo "===> Starting Docker build (Step 6)..."
docker compose build --no-cache
echo "Docker images built successfully."
echo

# --- Step 7: Start Docker containers ---
echo "===> Starting Docker containers (Step 7)..."
docker compose up -d
echo "Docker containers started successfully."
echo

echo "===================================="
echo "Script finished successfully! (Mode: $APP_MODE)"
echo "===================================="
