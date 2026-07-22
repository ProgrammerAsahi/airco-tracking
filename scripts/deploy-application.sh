#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
PREFIX="${AZURE_PREFIX:-aircontrack}"
EMAIL_LANG="${EMAIL_LANG:-zh}"
EMAIL_MIN_SEND_INTERVAL_SECONDS="${EMAIL_MIN_SEND_INTERVAL_SECONDS:-13}"
EMAIL_MAX_REPLICAS="${EMAIL_MAX_REPLICAS:-1}"
EMAIL_REPLY_TO="${EMAIL_REPLY_TO:-support@airco-tracker.eu}"
APP_BASE_URL="${APP_BASE_URL:-https://airco-tracker.eu}"
ACS_EMAIL_DOMAIN_NAME="${ACS_EMAIL_DOMAIN_NAME:-AzureManagedDomain}"
COUNTRIES="${COUNTRIES:-nl,fr}"
DEPLOYMENT_PAUSED="${DEPLOYMENT_PAUSED:-false}"
SCANNER_CRON_EXPRESSION="${SCANNER_CRON_EXPRESSION:-*/10 * * * *}"
PUBLISHER_CRON_EXPRESSION="${PUBLISHER_CRON_EXPRESSION:-* * * * *}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD 2>/dev/null || date -u +manual-%Y%m%d%H%M%S)}"

if [[ "$DEPLOYMENT_PAUSED" == "true" ]]; then
  SCANNER_CRON_EXPRESSION='0 0 1 1 *'
  PUBLISHER_CRON_EXPRESSION='0 0 1 1 *'
elif [[ "$DEPLOYMENT_PAUSED" != "false" ]]; then
  echo "DEPLOYMENT_PAUSED must be true or false." >&2
  exit 1
fi

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
command -v python3 >/dev/null || { echo "Python 3 is required." >&2; exit 1; }
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

communication_domain_id() {
  if [[ ! "$ACS_EMAIL_DOMAIN_NAME" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "ACS_EMAIL_DOMAIN_NAME contains unsupported characters." >&2
    return 1
  fi
  az resource show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACS_NAME" \
    --resource-type Microsoft.Communication/communicationServices \
    --query "properties.linkedDomains[?ends_with(@, '/domains/${ACS_EMAIL_DOMAIN_NAME}')]|[0]" \
    --output tsv
}

require_value() {
  if [ -z "$2" ]; then
    echo "Could not determine $1 in resource group $RESOURCE_GROUP." >&2
    exit 1
  fi
}

single_job_identity_name() {
  local job_name="$1"
  local identity_ids identity_count identity_id
  identity_ids="$(
    az containerapp job show \
      --name "$job_name" \
      --resource-group "$RESOURCE_GROUP" \
      --query 'identity.userAssignedIdentities | keys(@)' \
      --output tsv
  )"
  identity_count="$(printf '%s\n' "$identity_ids" | awk '{ for (i = 1; i <= NF; i++) n++ } END { print n + 0 }')"
  if [[ "$identity_count" != "1" ]]; then
    echo "Expected exactly one user-assigned identity on $job_name; found $identity_count." >&2
    return 1
  fi
  identity_id="$(printf '%s\n' "$identity_ids" | awk 'NF { print $1; exit }')"
  printf '%s\n' "${identity_id##*/}"
}

ACR_NAME="$(single_resource_name ACR_NAME Microsoft.ContainerRegistry/registries)"
require_value ACR_NAME "$ACR_NAME"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer --output tsv)}"
require_value ACR_LOGIN_SERVER "$ACR_LOGIN_SERVER"
ENVIRONMENT_NAME="$(single_resource_name CONTAINER_ENVIRONMENT_NAME Microsoft.App/managedEnvironments)"
require_value CONTAINER_ENVIRONMENT_NAME "$ENVIRONMENT_NAME"
SCANNER_IDENTITY_NAME="${SCANNER_IDENTITY_NAME:-${PREFIX}-scanner}"
RETENTION_IDENTITY_NAME="${RETENTION_IDENTITY_NAME:-${PREFIX}-retention}"
PUBLISHER_IDENTITY_NAME="${PUBLISHER_IDENTITY_NAME:-${PREFIX}-alert-publisher}"
FANOUT_IDENTITY_NAME="${FANOUT_IDENTITY_NAME:-${PREFIX}-alert-fanout}"
EMAIL_IDENTITY_NAME="${EMAIL_IDENTITY_NAME:-${PREFIX}-alert-email}"
DELIVERY_REPORT_IDENTITY_NAME="${DELIVERY_REPORT_IDENTITY_NAME:-${PREFIX}-alert-delivery-report}"
for identity_name in "$SCANNER_IDENTITY_NAME" "$RETENTION_IDENTITY_NAME" "$PUBLISHER_IDENTITY_NAME" "$FANOUT_IDENTITY_NAME" "$EMAIL_IDENTITY_NAME" "$DELIVERY_REPORT_IDENTITY_NAME"; do
  az identity show --name "$identity_name" --resource-group "$RESOURCE_GROUP" --output none
done
STORAGE_NAME="$(single_resource_name STORAGE_ACCOUNT_NAME Microsoft.Storage/storageAccounts)"
require_value STORAGE_ACCOUNT_NAME "$STORAGE_NAME"
SERVICE_BUS_NAMESPACE_NAME="$(single_resource_name SERVICE_BUS_NAMESPACE_NAME Microsoft.ServiceBus/namespaces)"
require_value SERVICE_BUS_NAMESPACE_NAME "$SERVICE_BUS_NAMESPACE_NAME"
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

PREVIOUS_IMAGE=""
PREVIOUS_SCANNER_IDENTITY_NAME="$SCANNER_IDENTITY_NAME"
PREVIOUS_RETENTION_IDENTITY_NAME="$RETENTION_IDENTITY_NAME"
if az containerapp job show --name airco-tracker-job --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
  PREVIOUS_IMAGE="$(
    az containerapp job show \
      --name airco-tracker-job \
      --resource-group "$RESOURCE_GROUP" \
      --query 'properties.template.containers[0].image' \
      --output tsv
  )"
  require_value PREVIOUS_IMAGE "$PREVIOUS_IMAGE"
  PREVIOUS_SCANNER_IDENTITY_NAME="$(single_job_identity_name airco-tracker-job)"
fi
if az containerapp job show --name airco-alert-retention-job --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
  PREVIOUS_RETENTION_IDENTITY_NAME="$(single_job_identity_name airco-alert-retention-job)"
fi

deploy_template() {
  local image="$1"
  local deployment_name="$2"
  local scanner_identity_name="${3:-$SCANNER_IDENTITY_NAME}"
  local retention_identity_name="${4:-$RETENTION_IDENTITY_NAME}"
  az deployment group create \
    --name "$deployment_name" \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$PROJECT_DIR/infra/job.bicep" \
    --parameters \
      containerImage="$image" \
      containerEnvironmentName="$ENVIRONMENT_NAME" \
      acrName="$ACR_NAME" \
      scannerIdentityName="$scanner_identity_name" \
      retentionIdentityName="$retention_identity_name" \
      publisherIdentityName="$PUBLISHER_IDENTITY_NAME" \
      fanoutIdentityName="$FANOUT_IDENTITY_NAME" \
      emailIdentityName="$EMAIL_IDENTITY_NAME" \
      deliveryReportIdentityName="$DELIVERY_REPORT_IDENTITY_NAME" \
      storageAccountName="$STORAGE_NAME" \
      serviceBusNamespaceName="$SERVICE_BUS_NAMESPACE_NAME" \
      communicationServiceName="$ACS_NAME" \
      keyVaultUrl="$KEY_VAULT_URL" \
      emailFrom="$EMAIL_FROM" \
      emailReplyTo="$EMAIL_REPLY_TO" \
      appBaseUrl="$APP_BASE_URL" \
      emailLang="$EMAIL_LANG" \
      emailMinSendIntervalSeconds="$EMAIL_MIN_SEND_INTERVAL_SECONDS" \
      emailMaxReplicas="$EMAIL_MAX_REPLICAS" \
      countries="$COUNTRIES" \
      scannerCronExpression="$SCANNER_CRON_EXPRESSION" \
      publisherCronExpression="$PUBLISHER_CRON_EXPRESSION" \
    --output none
}

start_and_verify_job() {
  local job_name="$1"
  local timeout_seconds="$2"
  local override_image="${3:-}"
  local start_args=(
    --name "$job_name"
    --resource-group "$RESOURCE_GROUP"
    --query name
    --output tsv
  )
  local candidate_template=""
  if [[ -n "$override_image" ]]; then
    # Any inline override makes Azure construct a fresh execution container;
    # omitted fields are not inherited. Render a complete, schema-filtered
    # execution template so command, args, environment, and resources remain
    # identical while only the immutable image changes.
    candidate_template="$(mktemp "${TMPDIR:-/tmp}/airco-job-execution.XXXXXX")"
    chmod 600 "$candidate_template"
    if ! az containerapp job show \
      --name "$job_name" \
      --resource-group "$RESOURCE_GROUP" \
      --query properties.template \
      --output json \
      | python3 "$PROJECT_DIR/scripts/render_job_execution_template.py" "$override_image" \
        > "$candidate_template"; then
      rm -f "$candidate_template"
      echo "Could not render a safe candidate execution template for $job_name." >&2
      return 1
    fi
    start_args+=(--yaml "$candidate_template")
  fi
  local execution_name
  if ! execution_name="$(az containerapp job start "${start_args[@]}")"; then
    [[ -z "$candidate_template" ]] || rm -f "$candidate_template"
    echo "Failed to start verification execution for $job_name." >&2
    return 1
  fi
  [[ -z "$candidate_template" ]] || rm -f "$candidate_template"
  if [[ -z "$execution_name" ]]; then
    echo "Failed to start verification execution for $job_name." >&2
    return 1
  fi
  echo "Verification execution: $job_name / $execution_name"
  local deadline=$(( $(date +%s) + timeout_seconds ))
  while true; do
    local status
    status="$(az containerapp job execution show --name "$job_name" --resource-group "$RESOURCE_GROUP" --job-execution-name "$execution_name" --query properties.status --output tsv 2>/dev/null || true)"
    if [[ "$status" == "Succeeded" ]]; then
      echo "$job_name verification succeeded."
      return 0
    fi
    if [[ "$status" == "Failed" ]]; then
      echo "$job_name verification failed. View logs with az containerapp job logs show." >&2
      return 1
    fi
    if [[ "$(date +%s)" -ge "$deadline" ]]; then
      echo "$job_name verification timed out (status: ${status:-unknown})." >&2
      return 1
    fi
    sleep 10
  done
}

DEPLOYMENT_STARTED=false
rollback_application() {
  local exit_code=$?
  trap - EXIT
  set +e
  if [[ "$DEPLOYMENT_STARTED" == "true" && -n "$PREVIOUS_IMAGE" ]]; then
    echo "Deployment failed; restoring all application workloads to $PREVIOUS_IMAGE and their previous scanner/retention identities." >&2
    deploy_template \
      "$PREVIOUS_IMAGE" \
      "airco-job-rollback-$(date -u +%Y%m%d%H%M%S)" \
      "$PREVIOUS_SCANNER_IDENTITY_NAME" \
      "$PREVIOUS_RETENTION_IDENTITY_NAME"
    start_and_verify_job airco-alert-reconciler-job 420
  fi
  exit "$exit_code"
}
trap rollback_application EXIT

az acr build \
  --registry "$ACR_NAME" \
  --image "airco-tracker:$IMAGE_TAG" \
  "$PROJECT_DIR"

# A one-off reconciler execution uses the candidate image but the currently
# deployed identity, secrets, and environment. It is read/reconcile-only and
# catches image/runtime incompatibility before any production definition moves.
if [[ -n "$PREVIOUS_IMAGE" ]]; then
  start_and_verify_job airco-alert-reconciler-job 420 "$IMAGE"
fi

DEPLOYMENT_STARTED=true
deploy_template "$IMAGE" "airco-job-${IMAGE_TAG:0:12}"

# Verify the data path in dependency order. Worker apps are validated by the
# targeted synthetic pipeline test after deployment.
start_and_verify_job airco-alert-reconciler-job 420
if [[ "$DEPLOYMENT_PAUSED" == "true" ]]; then
  echo "Scanner and publisher verification skipped while DEPLOYMENT_PAUSED=true."
else
  start_and_verify_job airco-tracker-job 480
  start_and_verify_job airco-alert-publisher-job 300
fi

trap - EXIT
echo "Deployed $IMAGE"
if [[ -n "$PREVIOUS_IMAGE" && "$PREVIOUS_IMAGE" != "$IMAGE" ]]; then
  echo "Rollback image: $PREVIOUS_IMAGE"
fi
