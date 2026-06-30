#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-nl-rg}"
EMAIL_TO="${EMAIL_TO:-asahi.lee.eu@outlook.com}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD 2>/dev/null || date -u +manual-%Y%m%d%H%M%S)}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

output() {
  az deployment group show \
    --name airco-foundation \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.outputs.$1.value" \
    --output tsv
}

ACR_NAME="$(output acrName)"
ACR_LOGIN_SERVER="$(output acrLoginServer)"
ENVIRONMENT_NAME="$(output containerEnvironmentName)"
IDENTITY_NAME="$(output identityName)"
STORAGE_NAME="$(output storageAccountName)"
ACS_NAME="$(output communicationServiceName)"
KEY_VAULT_URL="$(output keyVaultUrl)"
EMAIL_FROM="$(output senderAddress)"
IMAGE="$ACR_LOGIN_SERVER/airco-tracker:$IMAGE_TAG"

az acr build \
  --registry "$ACR_NAME" \
  --image "airco-tracker:$IMAGE_TAG" \
  "$PROJECT_DIR"

az deployment group create \
  --name "airco-job-${IMAGE_TAG:0:12}" \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$PROJECT_DIR/infra/job.bicep" \
  --parameters \
    containerImage="$IMAGE" \
    containerEnvironmentName="$ENVIRONMENT_NAME" \
    acrName="$ACR_NAME" \
    identityName="$IDENTITY_NAME" \
    storageAccountName="$STORAGE_NAME" \
    communicationServiceName="$ACS_NAME" \
    keyVaultUrl="$KEY_VAULT_URL" \
    emailFrom="$EMAIL_FROM" \
    emailTo="$EMAIL_TO" \
  --output none

az containerapp job start \
  --name airco-tracker-job \
  --resource-group "$RESOURCE_GROUP" \
  --output none

echo "Deployed $IMAGE"
