#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
EMAIL_LANG="${EMAIL_LANG:-zh}"
KEY_VAULT_SECRET_MAP="${KEY_VAULT_SECRET_MAP:-EMAIL_TO=notification-email}"
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
    emailLang="$EMAIL_LANG" \
    keyVaultEnvMap="$KEY_VAULT_SECRET_MAP" \
  --output none

# Start a verification run and wait for its result so a failed deployment
# surfaces as a non-zero exit code instead of silently reporting success.
EXECUTION_NAME="$(
  az containerapp job start \
    --name airco-tracker-job \
    --resource-group "$RESOURCE_GROUP" \
    --query name \
    --output tsv
)"

if [ -z "$EXECUTION_NAME" ]; then
  echo "Failed to start verification execution." >&2
  exit 1
fi

echo "Verification execution: $EXECUTION_NAME"
echo "Waiting for execution to complete..."

DEADLINE=$(( $(date +%s) + 480 ))  # job replicaTimeout=300s + margin
while true; do
  STATUS="$(
    az containerapp job execution show \
      --name airco-tracker-job \
      --resource-group "$RESOURCE_GROUP" \
      --job-execution-name "$EXECUTION_NAME" \
      --query properties.status \
      --output tsv 2>/dev/null || true
  )"
  if [ "$STATUS" = "Succeeded" ]; then
    echo "Verification succeeded."
    break
  fi
  if [ "$STATUS" = "Failed" ]; then
    echo "Verification execution failed. View logs:" >&2
    echo "  az containerapp job logs show -n airco-tracker-job -g $RESOURCE_GROUP --job-execution-name $EXECUTION_NAME" >&2
    exit 1
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "Verification execution timed out after 8 minutes (status: ${STATUS:-unknown})." >&2
    exit 1
  fi
  sleep 10
done

echo "Deployed $IMAGE"
