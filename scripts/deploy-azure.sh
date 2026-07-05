#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
LOCATION="${AZURE_LOCATION:-westeurope}"
PREFIX="${AZURE_PREFIX:-aircontrack}"
EMAIL_TO="${EMAIL_TO:-you@example.com}"
IMAGE_TAG="${IMAGE_TAG:-bootstrap-$(date -u +%Y%m%d%H%M%S)}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

if [[ -z "$EMAIL_TO" || "$EMAIL_TO" == "you@example.com" || "$EMAIL_TO" != *@* ]]; then
  echo "Set EMAIL_TO to the notification address before deploying." >&2
  exit 1
fi

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

KEY_VAULT_URL="$(
  az deployment group show \
    --name airco-foundation \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.outputs.keyVaultUrl.value \
    --output tsv
)"
KEY_VAULT_NAME="${KEY_VAULT_URL#https://}"
KEY_VAULT_NAME="${KEY_VAULT_NAME%%.*}"
KEY_VAULT_ID="$(az keyvault show --name "$KEY_VAULT_NAME" --query id --output tsv)"
USER_OBJECT_ID="$(az ad signed-in-user show --query id --output tsv)"
ROLE_ASSIGNMENT_ID="$(az role assignment list \
  --assignee "$USER_OBJECT_ID" \
  --scope "$KEY_VAULT_ID" \
  --role "Key Vault Secrets Officer" \
  --query '[0].id' \
  --output tsv)"
CREATED_ROLE_ASSIGNMENT=false
if [[ -z "$ROLE_ASSIGNMENT_ID" ]]; then
  ROLE_ASSIGNMENT_ID="$(az role assignment create \
    --assignee-object-id "$USER_OBJECT_ID" \
    --assignee-principal-type User \
    --scope "$KEY_VAULT_ID" \
    --role "Key Vault Secrets Officer" \
    --query id \
    --output tsv)"
  CREATED_ROLE_ASSIGNMENT=true
fi

cleanup() {
  unset EMAIL_TO
  if [[ "$CREATED_ROLE_ASSIGNMENT" == "true" ]]; then
    az role assignment delete --ids "$ROLE_ASSIGNMENT_ID" --only-show-errors || true
  fi
}
trap cleanup EXIT

for attempt in {1..12}; do
  if az keyvault secret set \
    --vault-name "$KEY_VAULT_NAME" \
    --name notification-email \
    --value "$EMAIL_TO" \
    --output none 2>/dev/null; then
    break
  fi
  if [[ "$attempt" == 12 ]]; then
    echo "Could not store the notification address after waiting for Azure RBAC propagation." >&2
    exit 1
  fi
  sleep 10
done
unset EMAIL_TO
if [[ "$CREATED_ROLE_ASSIGNMENT" == "true" ]]; then
  az role assignment delete --ids "$ROLE_ASSIGNMENT_ID" --only-show-errors
fi
trap - EXIT

echo "Foundation complete. Building and deploying the application..."
AZURE_RESOURCE_GROUP="$RESOURCE_GROUP" \
KEY_VAULT_SECRET_MAP="EMAIL_TO=notification-email" \
IMAGE_TAG="$IMAGE_TAG" \
  "$PROJECT_DIR/scripts/deploy-application.sh"

echo "Deployment complete."
echo "List executions: az containerapp job execution list -n airco-tracker-job -g $RESOURCE_GROUP -o table"
echo "View logs: az containerapp job logs show -n airco-tracker-job -g $RESOURCE_GROUP --follow"
