#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
EMAIL_LANG="${EMAIL_LANG:-zh}"
KEY_VAULT_SECRET_MAP="${KEY_VAULT_SECRET_MAP:-EMAIL_TO=notification-email}"
COUNTRIES="${COUNTRIES:-nl,fr}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD 2>/dev/null || date -u +manual-%Y%m%d%H%M%S)}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

single_resource_name() {
  local env_var="$1"
  local resource_type="$2"
  local configured="${!env_var:-}"
  if [ -n "$configured" ]; then
    echo "$configured"
    return
  fi

  local names
  names="$(az resource list \
    --resource-group "$RESOURCE_GROUP" \
    --resource-type "$resource_type" \
    --query "[].name" \
    --output tsv)"
  local count
  count="$(printf '%s\n' "$names" | awk 'NF { count++ } END { print count + 0 }')"
  if [ "$count" != "1" ]; then
    echo "Expected exactly one $resource_type in $RESOURCE_GROUP; found $count. Set $env_var explicitly." >&2
    return 1
  fi
  printf '%s\n' "$names" | awk 'NF { print; exit }'
}

runtime_identity_name() {
  if [ -n "${IDENTITY_NAME:-}" ]; then
    echo "$IDENTITY_NAME"
    return
  fi

  local names
  names="$(az identity list \
    --resource-group "$RESOURCE_GROUP" \
    --query "[?name!='airco-github-deployer'].name" \
    --output tsv)"
  local count
  count="$(printf '%s\n' "$names" | awk 'NF { count++ } END { print count + 0 }')"
  if [ "$count" != "1" ]; then
    echo "Expected exactly one runtime managed identity in $RESOURCE_GROUP; found $count. Set IDENTITY_NAME explicitly." >&2
    return 1
  fi
  printf '%s\n' "$names" | awk 'NF { print; exit }'
}

communication_domain_id() {
  az resource show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACS_NAME" \
    --resource-type Microsoft.Communication/communicationServices \
    --query "properties.linkedDomains[0]" \
    --output tsv
}

require_value() {
  if [ -z "$2" ]; then
    echo "Could not determine $1 in resource group $RESOURCE_GROUP." >&2
    exit 1
  fi
}

ACR_NAME="$(single_resource_name ACR_NAME Microsoft.ContainerRegistry/registries)"
require_value ACR_NAME "$ACR_NAME"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer --output tsv)}"
require_value ACR_LOGIN_SERVER "$ACR_LOGIN_SERVER"
ENVIRONMENT_NAME="$(single_resource_name CONTAINER_ENVIRONMENT_NAME Microsoft.App/managedEnvironments)"
require_value CONTAINER_ENVIRONMENT_NAME "$ENVIRONMENT_NAME"
IDENTITY_NAME="$(runtime_identity_name)"
require_value IDENTITY_NAME "$IDENTITY_NAME"
STORAGE_NAME="$(single_resource_name STORAGE_ACCOUNT_NAME Microsoft.Storage/storageAccounts)"
require_value STORAGE_ACCOUNT_NAME "$STORAGE_NAME"
ACS_NAME="$(single_resource_name COMMUNICATION_SERVICE_NAME Microsoft.Communication/communicationServices)"
require_value COMMUNICATION_SERVICE_NAME "$ACS_NAME"
KEY_VAULT_NAME="$(single_resource_name KEY_VAULT_NAME Microsoft.KeyVault/vaults)"
require_value KEY_VAULT_NAME "$KEY_VAULT_NAME"
KEY_VAULT_URL="${KEY_VAULT_URL:-$(az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$RESOURCE_GROUP" --query properties.vaultUri --output tsv)}"
require_value KEY_VAULT_URL "$KEY_VAULT_URL"
DOMAIN_ID="${COMMUNICATION_DOMAIN_ID:-$(communication_domain_id)}"
require_value COMMUNICATION_DOMAIN_ID "$DOMAIN_ID"
FROM_SENDER_DOMAIN="$(
  az resource show \
    --ids "$DOMAIN_ID" \
    --query "properties.fromSenderDomain" \
    --output tsv
)"
require_value FROM_SENDER_DOMAIN "$FROM_SENDER_DOMAIN"
EMAIL_FROM="${EMAIL_FROM:-DoNotReply@$FROM_SENDER_DOMAIN}"
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
    countries="$COUNTRIES" \
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
