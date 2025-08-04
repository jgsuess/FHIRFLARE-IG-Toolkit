#!/bin/bash
# Build FHIRFLARE-IG-Toolkit Docker image

# Build the image using the Dockerfile in the docker directory
docker build -f Dockerfile -t fhirflare-ig-toolkit:latest ..

echo "Docker image built successfully"