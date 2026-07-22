#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
LOCATION="${AZURE_LOCATION:-westeurope}"
PREFIX="${AZURE_PREFIX:-aircontrack}"
IMAGE_TAG="${IMAGE_TAG:-bootstrap-$(date -u +%Y%m%d%H%M%S)}"
FOUNDATION_ONLY="${AZURE_FOUNDATION_ONLY:-false}"

if [[ "$FOUNDATION_ONLY" != "true" && "$FOUNDATION_ONLY" != "false" ]]; then
  echo "AZURE_FOUNDATION_ONLY must be true or false." >&2
  exit 1
fi

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.Communication --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.EventGrid --wait
az provider register --namespace Microsoft.KeyVault --wait
az provider register --namespace Microsoft.ManagedIdentity --wait
az provider register --namespace Microsoft.Insights --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.Storage --wait
az provider register --namespace Microsoft.ServiceBus --wait

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

# The uniqueString algorithm is only a safe default for a brand-new
# environment.  Preserve the suffix of the deployed storage account during an
# upgrade so a template/toolchain change can never create a second data plane.
RESOURCE_TOKEN="${AZURE_RESOURCE_TOKEN:-}"
if [[ -z "$RESOURCE_TOKEN" ]]; then
  EXISTING_STORAGE_NAMES="$(
    az storage account list \
      --resource-group "$RESOURCE_GROUP" \
      --query "[?starts_with(name, 'aircostate')].name" \
      --output tsv
  )"
  EXISTING_STORAGE_COUNT="$(printf '%s\n' "$EXISTING_STORAGE_NAMES" | awk 'NF { count++ } END { print count + 0 }')"
  if [[ "$EXISTING_STORAGE_COUNT" -gt 1 ]]; then
    echo "Found multiple aircostate* accounts in $RESOURCE_GROUP. Set AZURE_RESOURCE_TOKEN explicitly." >&2
    exit 1
  fi
  if [[ "$EXISTING_STORAGE_COUNT" -eq 1 ]]; then
    EXISTING_STORAGE_NAME="$(printf '%s\n' "$EXISTING_STORAGE_NAMES" | awk 'NF { print; exit }')"
    RESOURCE_TOKEN="${EXISTING_STORAGE_NAME#aircostate}"
  fi
fi

FOUNDATION_PARAMETERS=(prefix="$PREFIX" location="$LOCATION")
if [[ -n "$RESOURCE_TOKEN" ]]; then
  if [[ ! "$RESOURCE_TOKEN" =~ ^[a-z0-9]{8}$ ]]; then
    echo "AZURE_RESOURCE_TOKEN must contain exactly eight lowercase letters or digits." >&2
    exit 1
  fi
  FOUNDATION_PARAMETERS+=(resourceToken="$RESOURCE_TOKEN")
fi

# Preserve an already-linked customer-managed sender domain. Without this,
# redeploying foundation after DNS verification would silently disconnect it.
CUSTOM_EMAIL_DOMAIN_ID="${ACS_CUSTOM_EMAIL_DOMAIN_ID:-}"
if [[ -z "$CUSTOM_EMAIL_DOMAIN_ID" && -n "$RESOURCE_TOKEN" ]]; then
  COMMUNICATION_SERVICE_NAME="${PREFIX}-acs-${RESOURCE_TOKEN}"
  COMMUNICATION_SERVICE_COUNT="$(
    az resource list \
      --resource-group "$RESOURCE_GROUP" \
      --resource-type Microsoft.Communication/communicationServices \
      --query "[?name=='${COMMUNICATION_SERVICE_NAME}'] | length(@)" \
      --output tsv
  )"
  if [[ "$COMMUNICATION_SERVICE_COUNT" == "1" ]]; then
    # An existing linked custom domain is production configuration. Once the
    # service exists, a failed read must abort rather than silently deploying
    # only AzureManagedDomain and disconnecting the verified sender.
    CUSTOM_EMAIL_DOMAIN_ID="$(
      az resource show \
        --resource-group "$RESOURCE_GROUP" \
        --name "$COMMUNICATION_SERVICE_NAME" \
        --resource-type Microsoft.Communication/communicationServices \
        --query "properties.linkedDomains[?!ends_with(@, '/domains/AzureManagedDomain')]|[0]" \
        --output tsv
    )"
  elif [[ "$COMMUNICATION_SERVICE_COUNT" != "0" ]]; then
    echo "Found an unexpected number of communication services: $COMMUNICATION_SERVICE_COUNT" >&2
    exit 1
  fi
fi
if [[ -n "$CUSTOM_EMAIL_DOMAIN_ID" ]]; then
  FOUNDATION_PARAMETERS+=(customEmailDomainId="$CUSTOM_EMAIL_DOMAIN_ID")
fi

# Keep an existing operations receiver on repeat foundation deployments. The
# address is passed as a secure ARM parameter and never committed to source.
OPERATIONS_ALERT_EMAIL="${AZURE_OPERATIONS_ALERT_EMAIL:-}"
if [[ -z "$OPERATIONS_ALERT_EMAIL" ]]; then
  ACTION_GROUP_COUNT="$(
    az resource list \
      --resource-group "$RESOURCE_GROUP" \
      --resource-type Microsoft.Insights/actionGroups \
      --query "[?name=='${PREFIX}-operations-alerts'] | length(@)" \
      --output tsv
  )"
  if [[ "$ACTION_GROUP_COUNT" == "1" ]]; then
    # Once an action group exists, a failed read is a deployment error. Do not
    # silently turn a transient CLI/RBAC failure into removal of the receiver.
    OPERATIONS_ALERT_EMAIL="$(
      az monitor action-group show \
        --resource-group "$RESOURCE_GROUP" \
        --name "${PREFIX}-operations-alerts" \
        --query 'emailReceivers[0].emailAddress' \
        --output tsv
    )"
    if [[ -z "$OPERATIONS_ALERT_EMAIL" ]]; then
      echo "Existing operations action group has no email receiver. Set AZURE_OPERATIONS_ALERT_EMAIL explicitly." >&2
      exit 1
    fi
  elif [[ "$ACTION_GROUP_COUNT" != "0" ]]; then
    echo "Found an unexpected number of operations action groups: $ACTION_GROUP_COUNT" >&2
    exit 1
  fi
fi
if [[ -n "$OPERATIONS_ALERT_EMAIL" ]]; then
  FOUNDATION_PARAMETERS+=(operationsAlertEmail="$OPERATIONS_ALERT_EMAIL")
fi

# Existing production environments may have equivalent web-identity grants
# under legacy random assignment IDs. Asking ARM to create the same
# principal/role/scope tuple under a deterministic ID returns
# RoleAssignmentExists, so preserve those assignments during the split. The
# Owner-only migration script validates them before removing only obsolete
# broad grants.
if az identity show \
  --name "${PREFIX}-identity" \
  --resource-group "$RESOURCE_GROUP" \
  --output none 2>/dev/null; then
  FOUNDATION_PARAMETERS+=(manageSharedIdentityRbac=false)
fi

# Secret values are created and rotated outside Bicep. On an existing
# environment, enable the per-secret RBAC resources only after every secret
# referenced by a runtime is present. This avoids a greenfield foundation
# deployment failing before operators have provisioned the initial values.
SECRET_SCOPED_RBAC_MODE="${AZURE_MANAGE_SECRET_SCOPED_KEY_VAULT_RBAC:-auto}"
if [[ "$SECRET_SCOPED_RBAC_MODE" != "auto" && "$SECRET_SCOPED_RBAC_MODE" != "true" && "$SECRET_SCOPED_RBAC_MODE" != "false" ]]; then
  echo "AZURE_MANAGE_SECRET_SCOPED_KEY_VAULT_RBAC must be auto, true, or false." >&2
  exit 1
fi
if [[ -n "$RESOURCE_TOKEN" && "$SECRET_SCOPED_RBAC_MODE" != "false" ]]; then
  KEY_VAULT_ID="$(az keyvault show --resource-group "$RESOURCE_GROUP" --name "aircokv${RESOURCE_TOKEN}" --query id --output tsv 2>/dev/null || true)"
  if [[ -n "$KEY_VAULT_ID" ]]; then
    REQUIRED_SECRET_NAMES=(
      unsubscribe-signing-key
      withdrawal-signing-key
      auth-code-hmac-pepper
      awin-publisher-api-token
      aliexpress-app-key
      aliexpress-app-secret
    )
    missing_secrets=()
    for secret_name in "${REQUIRED_SECRET_NAMES[@]}"; do
      if ! az resource show \
        --ids "$KEY_VAULT_ID/secrets/$secret_name" \
        --api-version 2023-07-01 \
        --output none 2>/dev/null; then
        missing_secrets+=("$secret_name")
      fi
    done
    if [[ "${#missing_secrets[@]}" -eq 0 ]]; then
      FOUNDATION_PARAMETERS+=(manageSecretScopedKeyVaultRbac=true)
    elif [[ "$SECRET_SCOPED_RBAC_MODE" == "true" ]]; then
      echo "Cannot enable secret-scoped RBAC; missing Key Vault secrets: ${missing_secrets[*]}" >&2
      exit 1
    else
      echo "Secret-scoped Key Vault RBAC deferred; missing: ${missing_secrets[*]}"
    fi
  elif [[ "$SECRET_SCOPED_RBAC_MODE" == "true" ]]; then
    echo "Cannot enable secret-scoped RBAC before the Key Vault exists." >&2
    exit 1
  fi
elif [[ "$SECRET_SCOPED_RBAC_MODE" == "true" ]]; then
  echo "Set AZURE_RESOURCE_TOKEN when explicitly enabling secret-scoped RBAC." >&2
  exit 1
fi

az deployment group create \
  --name airco-foundation \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$PROJECT_DIR/infra/foundation.bicep" \
  --parameters "${FOUNDATION_PARAMETERS[@]}" \
  --output none

if [[ "$FOUNDATION_ONLY" == "true" ]]; then
  echo "Foundation complete. AZURE_FOUNDATION_ONLY=true; application deployment and identity-migration checks were intentionally skipped."
  exit 0
fi

echo "Foundation complete. Building and deploying the application..."
AZURE_RESOURCE_GROUP="$RESOURCE_GROUP" \
AZURE_PREFIX="$PREFIX" \
IMAGE_TAG="$IMAGE_TAG" \
  "$PROJECT_DIR/scripts/deploy-application.sh"

# This full-infrastructure path is Owner-operated. Show the exact legacy
# grants only after every replacement workload exists. A greenfield backend
# bootstrap legitimately runs before the web repository has created its
# cleanup job, so defer (rather than fail) that read-only audit in that case.
# Apply always remains a separate explicit Owner action after both repos pass
# smoke tests.
WEB_RETENTION_JOB_NAME="${AZURE_WEB_RETENTION_JOB_NAME:-airco-web-retention-cleanup}"
if az containerapp job show \
  --name "$WEB_RETENTION_JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --output none 2>/dev/null; then
  AZURE_RESOURCE_GROUP="$RESOURCE_GROUP" \
  AZURE_PREFIX="$PREFIX" \
    "$PROJECT_DIR/scripts/migrate-runtime-identities.sh"
else
  echo "Identity-migration dry run deferred: $WEB_RETENTION_JOB_NAME is not deployed yet."
  echo "Deploy the web repository, then run scripts/migrate-runtime-identities.sh before --apply."
fi

echo "Deployment complete."
echo "After web smoke tests, remove verified legacy grants with: scripts/migrate-runtime-identities.sh --apply"
echo "List executions: az containerapp job execution list -n airco-tracker-job -g $RESOURCE_GROUP -o table"
echo "View logs: az containerapp job logs show -n airco-tracker-job -g $RESOURCE_GROUP --follow"
