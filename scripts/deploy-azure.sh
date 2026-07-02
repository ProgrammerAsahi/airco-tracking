#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-nl-rg}"
LOCATION="${AZURE_LOCATION:-westeurope}"
PREFIX="${AZURE_PREFIX:-aircontrack}"
EMAIL_TO="${EMAIL_TO:-you@example.com}"
IMAGE_TAG="${IMAGE_TAG:-bootstrap-$(date -u +%Y%m%d%H%M%S)}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.Communication --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.KeyVault --wait
az provider register --namespace Microsoft.ManagedIdentity --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.Storage --wait

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

az deployment group create \
  --name airco-foundation \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$PROJECT_DIR/infra/foundation.bicep" \
  --parameters prefix="$PREFIX" location="$LOCATION" \
  --output none

echo "Foundation complete. Building and deploying the application..."
AZURE_RESOURCE_GROUP="$RESOURCE_GROUP" \
EMAIL_TO="$EMAIL_TO" \
IMAGE_TAG="$IMAGE_TAG" \
  "$PROJECT_DIR/scripts/deploy-application.sh"

echo "Deployment complete."
echo "Recipient: $EMAIL_TO"
echo "List executions: az containerapp job execution list -n airco-tracker-job -g $RESOURCE_GROUP -o table"
echo "View logs: az containerapp job logs show -n airco-tracker-job -g $RESOURCE_GROUP --follow"
