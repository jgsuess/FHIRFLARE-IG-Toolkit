#!/bin/bash
#
# FHIRFLARE-IG-Toolkit Installation Script
# 
# Description:
#   This script installs the FHIRFLARE-IG-Toolkit Helm chart into a Kubernetes cluster.
#   It adds the FHIRFLARE-IG-Toolkit Helm repository and then installs the chart
#   in the 'flare' namespace, creating the namespace if it doesn't exist.
#
# Usage:
#   ./install.sh
#
# Requirements:
#   - Helm (v3+)
#   - kubectl configured with access to your Kubernetes cluster
#

# Add the FHIRFLARE-IG-Toolkit Helm repository
helm repo add flare https://jgsuess.github.io/FHIRFLARE-IG-Toolkit/

# Install the FHIRFLARE-IG-Toolkit chart in the 'flare' namespace

helm install flare/fhirflare-ig-toolkit --namespace flare --create-namespace --generate-name --set hapi-fhir-jpaserver.postgresql.primary.persistence.storageClass=gp2 --atomic