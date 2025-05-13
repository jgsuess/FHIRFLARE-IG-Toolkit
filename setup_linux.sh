#!/bin/bash

# --- Configuration ---
REPO_URL="https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git"
CLONE_DIR="hapi-fhir-jpaserver"
SOURCE_CONFIG_DIR="hapi-fhir-Setup" # Assuming this is relative to the script's parent
CONFIG_FILE="application.yaml"

# --- Define Paths ---
# Note: Adjust SOURCE_CONFIG_PATH if SOURCE_CONFIG_DIR is not a sibling directory
# This assumes the script is run from a directory, and hapi-fhir-setup is at the same level
SOURCE_CONFIG_PATH="../${SOURCE_CONFIG_DIR}/target/classes/${CONFIG_FILE}"
DEST_CONFIG_PATH="${CLONE_DIR}/target/classes/${CONFIG_FILE}"

APP_MODE=""

# --- Error Handling Function ---
handle_error() {
    echo "------------------------------------"
    echo "An error occurred: $1"
    echo "Script aborted."
    echo "------------------------------------"
    # Removed 'read -p "Press Enter to exit..."' as it's not typical for non-interactive CI/CD
    exit 1
}

# === Prompt for Installation Mode ===
get_mode_choice() {
    echo "Select Installation Mode:"
    echo "1. Standalone (Includes local HAPI FHIR Server - Requires Git & Maven)"
    echo "2. Lite (Excludes local HAPI FHIR Server - No Git/Maven needed)"

    while true; do
        read -r -p "Enter your choice (1 or 2): " choice
        case "$choice" in
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
}

# Call the function to get mode choice
get_mode_choice

# === Conditionally Execute HAPI Setup ===
if [ "$APP_MODE" = "standalone" ]; then
    echo "Running Standalone setup including HAPI FHIR..."
    echo

    # --- Step 0: Clean up previous clone (optional) ---
    echo "Checking for existing directory: $CLONE_DIR"
    if [ -d "$CLONE_DIR" ]; then
        echo "Found existing directory, removing it..."
        rm -rf "$CLONE_DIR"
        if [ $? -ne 0 ]; then
            handle_error "Failed to remove existing directory: $CLONE_DIR"
        fi
        echo "Existing directory removed."
    else
        echo "Directory does not exist, proceeding with clone."
    fi
    echo

    # --- Step 1: Clone the HAPI FHIR server repository ---
    echo "Cloning repository: $REPO_URL into $CLONE_DIR..."
    git clone "$REPO_URL" "$CLONE_DIR"
    if [ $? -ne 0 ]; then
        handle_error "Failed to clone repository. Check Git installation and network connection."
    fi
    echo "Repository cloned successfully."
    echo

    # --- Step 2: Navigate into the cloned directory ---
    echo "Changing directory to $CLONE_DIR..."
    cd "$CLONE_DIR" || handle_error "Failed to change directory to $CLONE_DIR."
    echo "Current directory: $(pwd)"
    echo

    # --- Step 3: Build the HAPI server using Maven ---
    echo "===> Starting Maven build (Step 3)..."
    mvn clean package -DskipTests=true -Pboot
    if [ $? -ne 0 ]; then
        echo "ERROR: Maven build failed."
        cd ..
        handle_error "Maven build process resulted in an error."
    fi
    echo "Maven build completed successfully."
    echo

    # --- Step 4: Copy the configuration file ---
    echo "===> Starting file copy (Step 4)..."
    echo "Copying configuration file..."
    # Corrected SOURCE_CONFIG_PATH to be relative to the new current directory ($CLONE_DIR)
    # This assumes the original script's SOURCE_CONFIG_PATH was relative to its execution location
    # If SOURCE_CONFIG_DIR is ../hapi-fhir-setup relative to script's original location:
    # Then from within CLONE_DIR, it becomes ../../hapi-fhir-setup
    # We defined SOURCE_CONFIG_PATH earlier relative to the script start.
    # So, when inside CLONE_DIR, the path from original script location should be used.
    # The original script had: set SOURCE_CONFIG_PATH=..\%SOURCE_CONFIG_DIR%\target\classes\%CONFIG_FILE%
    # And then: xcopy "%SOURCE_CONFIG_PATH%" "target\classes\"
    # This implies SOURCE_CONFIG_PATH is relative to the original script's location, not the $CLONE_DIR
    # Therefore, we need to construct the correct relative path from *within* $CLONE_DIR back to the source.
    # Assuming the script is in dir X, and SOURCE_CONFIG_DIR is ../hapi-fhir-setup from X.
    # So, hapi-fhir-setup is a sibling of X's parent.
    # If CLONE_DIR is also in X, then from within CLONE_DIR, the path is ../ + original SOURCE_CONFIG_PATH
    # For simplicity and robustness, let's use an absolute path or a more clearly defined relative path from the start.
    # The original `SOURCE_CONFIG_PATH=..\%SOURCE_CONFIG_DIR%\target\classes\%CONFIG_FILE%` implies
    # that `hapi-fhir-setup` is a sibling of the directory where the script *is being run from*.

    # Let's assume the script is run from the root of FHIRFLARE-IG-Toolkit.
    # And hapi-fhir-setup is also in the root, next to this script.
    # Then SOURCE_CONFIG_PATH would be ./hapi-fhir-setup/target/classes/application.yaml
    # And from within ./hapi-fhir-jpaserver/, the path would be ../hapi-fhir-setup/target/classes/application.yaml

    # The original batch file sets SOURCE_CONFIG_PATH as "..\%SOURCE_CONFIG_DIR%\target\classes\%CONFIG_FILE%"
    # And COPIES it to "target\classes\" *while inside CLONE_DIR*.
    # This means the source path is relative to where the *cd %CLONE_DIR%* happened from.
    # Let's make it relative to the script's initial execution directory.
    INITIAL_SCRIPT_DIR=$(pwd)
    ABSOLUTE_SOURCE_CONFIG_PATH="${INITIAL_SCRIPT_DIR}/../${SOURCE_CONFIG_DIR}/target/classes/${CONFIG_FILE}" # This matches the ..\ logic

    echo "Source: $ABSOLUTE_SOURCE_CONFIG_PATH"
    echo "Destination: target/classes/$CONFIG_FILE"

    if [ ! -f "$ABSOLUTE_SOURCE_CONFIG_PATH" ]; then
        echo "WARNING: Source configuration file not found at $ABSOLUTE_SOURCE_CONFIG_PATH."
        echo "The script will continue, but the server might use default configuration."
    else
        cp "$ABSOLUTE_SOURCE_CONFIG_PATH" "target/classes/"
        if [ $? -ne 0 ]; then
            echo "WARNING: Failed to copy configuration file. Check if the source file exists and permissions."
            echo "The script will continue, but the server might use default configuration."
        else
            echo "Configuration file copied successfully."
        fi
    fi
    echo

    # --- Step 5: Navigate back to the parent directory ---
    echo "===> Changing directory back (Step 5)..."
    cd .. || handle_error "Failed to change back to the parent directory."
    echo "Current directory: $(pwd)"
    echo

else # APP_MODE is "lite"
    echo "Running Lite setup, skipping HAPI FHIR build..."
    # Ensure the hapi-fhir-jpaserver directory doesn't exist or is empty if Lite mode is chosen
    if [ -d "$CLONE_DIR" ]; then
        echo "Found existing HAPI directory ($CLONE_DIR) in Lite mode. Removing it..."
        rm -rf "$CLONE_DIR"
    fi
    # Create empty target directories expected by Dockerfile COPY, even if not used
    mkdir -p "${CLONE_DIR}/target/classes"
    mkdir -p "${CLONE_DIR}/custom" # This was in the original batch, ensure it's here
    # Create a placeholder empty WAR file and application.yaml to satisfy Dockerfile COPY
    touch "${CLONE_DIR}/target/ROOT.war"
    touch "${CLONE_DIR}/target/classes/application.yaml"
    echo "Placeholder files and directories created for Lite mode build in $CLONE_DIR."
    echo
fi

# === Modify docker-compose.yml to set APP_MODE ===
echo "Updating docker-compose.yml with APP_MODE=$APP_MODE..."
DOCKER_COMPOSE_TMP="docker-compose.yml.tmp"
DOCKER_COMPOSE_ORIG="docker-compose.yml"

cat << EOF > "$DOCKER_COMPOSE_TMP"
version: '3.8'
services:
  fhirflare:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "5000:5000"
      - "8080:8080" # Keep port exposed, even if Tomcat isn't running useful stuff in Lite
    volumes:
      - ./instance:/app/instance
      - ./static/uploads:/app/static/uploads
      - ./instance/hapi-h2-data/:/app/h2-data # Keep volume mounts consistent
      - ./logs:/app/logs
    environment:
      - FLASK_APP=app.py
      - FLASK_ENV=development
      - NODE_PATH=/usr/lib/node_modules
      - APP_MODE=${APP_MODE}
      - APP_BASE_URL=https://localhost:5000
      - HAPI_FHIR_URL=https://localhost:8080/fhir
    command: supervisord -c /etc/supervisord.conf
EOF

if [ ! -f "$DOCKER_COMPOSE_TMP" ]; then
    handle_error "Failed to create temporary docker-compose file ($DOCKER_COMPOSE_TMP)."
fi

# Replace the original docker-compose.yml
mv "$DOCKER_COMPOSE_TMP" "$DOCKER_COMPOSE_ORIG"
echo "docker-compose.yml updated successfully."
echo

# --- Step 6: Build Docker images ---
echo "===> Starting Docker build (Step 6)..."
docker-compose build --no-cache
if [ $? -ne 0 ]; then
    handle_error "Docker Compose build failed. Check Docker installation and docker-compose.yml file."
fi
echo "Docker images built successfully."
echo

# --- Step 7: Start Docker containers ---
echo "===> Starting Docker containers (Step 7)..."
docker-compose up -d
if [ $? -ne 0 ]; then
    handle_error "Docker Compose up failed. Check Docker installation and container configurations."
fi
echo "Docker containers started successfully."
echo

echo "===================================="
echo "Script finished successfully! (Mode: $APP_MODE)"
echo "===================================="
exit 0
