#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
LOCATION="${AZURE_LOCATION:-westeurope}"
PREFIX="${AZURE_PREFIX:-aircontrack}"
IMAGE_TAG="${IMAGE_TAG:-bootstrap-$(date -u +%Y%m%d%H%M%S)}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.Communication --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
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

if az identity show \
  --name "${PREFIX}-identity" \
  --resource-group "$RESOURCE_GROUP" \
  --output none 2>/dev/null; then
  # Older environments provisioned these assignments outside Bicep with
  # random names. Recreating the same principal/role/scope under deterministic
  # names would fail with RoleAssignmentExists.
  FOUNDATION_PARAMETERS+=(manageSharedIdentityRbac=false)
fi

az deployment group create \
  --name airco-foundation \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$PROJECT_DIR/infra/foundation.bicep" \
  --parameters "${FOUNDATION_PARAMETERS[@]}" \
  --output none

echo "Foundation complete. Building and deploying the application..."
AZURE_RESOURCE_GROUP="$RESOURCE_GROUP" \
AZURE_PREFIX="$PREFIX" \
IMAGE_TAG="$IMAGE_TAG" \
  "$PROJECT_DIR/scripts/deploy-application.sh"

echo "Deployment complete."
echo "List executions: az containerapp job execution list -n airco-tracker-job -g $RESOURCE_GROUP -o table"
echo "View logs: az containerapp job logs show -n airco-tracker-job -g $RESOURCE_GROUP --follow"
