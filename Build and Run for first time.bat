@echo off
setlocal enabledelayedexpansion

REM --- Configuration ---
set REPO_URL=https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git
set CLONE_DIR=hapi-fhir-jpaserver
set SOURCE_CONFIG_DIR=hapi-fhir-setup
set CONFIG_FILE=application.yaml

REM --- Define Paths ---
REM Source path assumes 'hapi-fhir-setup' directory is in the same parent directory as this script
set SOURCE_CONFIG_PATH=..\%SOURCE_CONFIG_DIR%\target\classes\%CONFIG_FILE%
REM Destination path inside the cloned directory's build output
set DEST_CONFIG_PATH=%CLONE_DIR%\target\classes\%CONFIG_FILE%

echo Starting the build and run process...
echo.

REM --- Step 0: Clean up previous clone (optional, prevents clone error if dir exists) ---
echo Checking for existing directory: %CLONE_DIR%
if exist "%CLONE_DIR%" (
    echo Found existing directory, removing it...
    rmdir /s /q "%CLONE_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to remove existing directory: %CLONE_DIR%
        goto :error
    )
    echo Existing directory removed.
) else (
    echo Directory does not exist, proceeding with clone.
)
echo.

REM --- Step 1: Clone the HAPI FHIR server repository ---
echo Cloning repository: %REPO_URL% into %CLONE_DIR%...
git clone "%REPO_URL%" "%CLONE_DIR%"
if errorlevel 1 (
    echo ERROR: Failed to clone repository. Check Git installation and network connection.
    goto :error
)
echo Repository cloned successfully.
echo.

REM --- Step 2: Navigate into the cloned directory ---
echo Changing directory to %CLONE_DIR%...
cd "%CLONE_DIR%"
if errorlevel 1 (
    echo ERROR: Failed to change directory to %CLONE_DIR%.
    goto :error
)
echo Current directory: %CD%
echo.

REM --- Step 3: Build the HAPI server using Maven ---
echo ===> Starting Maven build (Step 3)...
REM Ensure mvn is in your system PATH
REM Using 'cmd /c "..."' to run Maven in a separate cmd instance
cmd /c "mvn clean package -DskipTests=true -Pboot"
REM Removed period from the end of the next echo statement to avoid syntax error
echo ===> Maven command finished (executed via cmd /c) Checking error level
REM Note: Error level check after cmd /c might reflect the exit code of cmd.exe itself,
REM but typically it passes through the error level of the command run within it (mvn).
if errorlevel 1 (
    echo ERROR: Maven build failed or cmd \c failed
    echo Check Maven installation and project configuration. Error
    cd ..
    goto :error
)
echo Maven build completed successfully. ErrorLevel: %errorlevel%
echo.

REM --- Step 4: Copy the configuration file ---
echo ===> Starting file copy (Step 4)...
REM Assumes the source file exists at ..\hapi-fhir-setup\target\classes\application.yaml relative to the script's location
REM Copies it into the target\classes directory created by the Maven build.
echo Copying configuration file...
echo Source: %SOURCE_CONFIG_PATH%
echo Destination: target\classes\%CONFIG_FILE%
xcopy "%SOURCE_CONFIG_PATH%" "target\classes\" /Y /I
echo ===> xcopy command finished. Checking error level...
if errorlevel 1 (
    echo WARNING: Failed to copy configuration file. Check if the source file exists at the expected location!
    echo The script will continue, but the server might use default configuration.
    REM Decide if this should be a fatal error (goto :error) or just a warning
    REM goto :error
) else (
    echo Configuration file copied successfully. ErrorLevel: %errorlevel%
)
echo.


REM --- Step 5: Navigate back to the parent directory ---
echo ===> Changing directory back (Step 5)...
cd ..
if errorlevel 1 (
    echo ERROR: Failed to change back to the parent directory. ErrorLevel: %errorlevel%
    goto :error
)
echo Current directory: %CD%
echo.

REM --- Step 6: Build Docker images ---
echo ===> Starting Docker build (Step 6)...
REM Ensure docker-compose is in your system PATH
docker-compose build --no-cache
if errorlevel 1 (
    echo ERROR: Docker Compose build failed. Check Docker installation and docker-compose.yml file. ErrorLevel: %errorlevel%
    goto :error
)
echo Docker images built successfully. ErrorLevel: %errorlevel%
echo.

REM --- Step 7: Start Docker containers ---
echo ===> Starting Docker containers (Step 7)...
docker-compose up -d
if errorlevel 1 (
    echo ERROR: Docker Compose up failed. Check Docker installation and container configurations. ErrorLevel: %errorlevel%
    goto :error
)
echo Docker containers started successfully. ErrorLevel: %errorlevel%
echo.

echo ====================================
echo Script finished successfully!
echo ====================================
goto :eof

:error
echo ------------------------------------
echo An error occurred. Script aborted.
echo ------------------------------------
pause
exit /b 1

:eof
echo Script execution finished.
pause
