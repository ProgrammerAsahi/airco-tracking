#!/usr/bin/env bash
set -euo pipefail

# Safe two-phase least-privilege migration. Run without --apply first. The
# script refuses to remove a legacy grant until replacement identities, their
# workload bindings, and every exact replacement RBAC assignment are verified.

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
PREFIX="${AZURE_PREFIX:-aircontrack}"
WEB_APP_NAME="${AZURE_WEB_APP_NAME:-airco-tracking-web}"
EMAIL_APP_NAME="${AZURE_EMAIL_APP_NAME:-airco-alert-email-worker}"
WEB_RETENTION_JOB_NAME="${AZURE_WEB_RETENTION_JOB_NAME:-airco-web-retention-cleanup}"
APPLY=false
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=true
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--apply]" >&2
  exit 2
fi

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

single_name() {
  local type="$1"
  local names count
  names="$(az resource list -g "$RESOURCE_GROUP" --resource-type "$type" --query '[].name' -o tsv)"
  count="$(printf '%s\n' "$names" | awk 'NF { n++ } END { print n + 0 }')"
  [[ "$count" == "1" ]] || { echo "Expected one $type, found $count." >&2; exit 1; }
  printf '%s\n' "$names" | awk 'NF { print; exit }'
}

role_count() {
  az role assignment list \
    --assignee-object-id "$1" \
    --scope "$2" \
    --query "[?roleDefinitionName=='$3'] | length(@)" \
    -o tsv
}

require_role() {
  local principal="$1" scope="$2" role="$3"
  local count
  count="$(role_count "$principal" "$scope" "$role")"
  [[ "$count" -ge 1 ]] || {
    echo "Missing replacement role for principal $principal: $role at $scope" >&2
    exit 1
  }
}

require_identity_binding() {
  local resource_kind="$1" resource_name="$2" expected_id="$3"
  local actual_ids actual_count actual_id actual_id_lower expected_id_lower
  if [[ "$resource_kind" == "app" ]]; then
    actual_ids="$(az containerapp show -g "$RESOURCE_GROUP" -n "$resource_name" --query "identity.userAssignedIdentities | keys(@)" -o tsv)"
  else
    actual_ids="$(az containerapp job show -g "$RESOURCE_GROUP" -n "$resource_name" --query "identity.userAssignedIdentities | keys(@)" -o tsv)"
  fi
  actual_count="$(printf '%s\n' "$actual_ids" | awk '{ for (i = 1; i <= NF; i++) n++ } END { print n + 0 }')"
  [[ "$actual_count" == "1" ]] || {
    echo "$resource_name must use exactly one user-assigned identity; found $actual_count." >&2
    exit 1
  }
  actual_id="$(printf '%s\n' "$actual_ids" | awk 'NF { print $1; exit }')"
  actual_id_lower="$(printf '%s' "$actual_id" | tr '[:upper:]' '[:lower:]')"
  expected_id_lower="$(printf '%s' "$expected_id" | tr '[:upper:]' '[:lower:]')"
  [[ "$actual_id_lower" == "$expected_id_lower" ]] || {
    echo "$resource_name is not using expected identity $expected_id." >&2
    exit 1
  }
}

WEB_NAME="${PREFIX}-identity"
SCANNER_NAME="${PREFIX}-scanner"
RETENTION_NAME="${PREFIX}-retention"
WEB_RETENTION_NAME="${PREFIX}-web-retention"
EMAIL_NAME="${PREFIX}-alert-email"

WEB_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$WEB_NAME" --query id -o tsv)"
WEB_PRINCIPAL="$(az identity show -g "$RESOURCE_GROUP" -n "$WEB_NAME" --query principalId -o tsv)"
SCANNER_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$SCANNER_NAME" --query id -o tsv)"
SCANNER_PRINCIPAL="$(az identity show -g "$RESOURCE_GROUP" -n "$SCANNER_NAME" --query principalId -o tsv)"
RETENTION_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$RETENTION_NAME" --query id -o tsv)"
RETENTION_PRINCIPAL="$(az identity show -g "$RESOURCE_GROUP" -n "$RETENTION_NAME" --query principalId -o tsv)"
WEB_RETENTION_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$WEB_RETENTION_NAME" --query id -o tsv)"
WEB_RETENTION_PRINCIPAL="$(az identity show -g "$RESOURCE_GROUP" -n "$WEB_RETENTION_NAME" --query principalId -o tsv)"
EMAIL_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$EMAIL_NAME" --query id -o tsv)"
EMAIL_PRINCIPAL="$(az identity show -g "$RESOURCE_GROUP" -n "$EMAIL_NAME" --query principalId -o tsv)"

STORAGE="$(single_name Microsoft.Storage/storageAccounts)"
ACR="$(single_name Microsoft.ContainerRegistry/registries)"
VAULT="$(single_name Microsoft.KeyVault/vaults)"
COMMUNICATION="$(single_name Microsoft.Communication/communicationServices)"
STORAGE_ID="$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE" --query id -o tsv)"
ACR_ID="$(az acr show -g "$RESOURCE_GROUP" -n "$ACR" --query id -o tsv)"
VAULT_ID="$(az keyvault show -g "$RESOURCE_GROUP" -n "$VAULT" --query id -o tsv)"
COMMUNICATION_ID="$(az resource show -g "$RESOURCE_GROUP" -n "$COMMUNICATION" --resource-type Microsoft.Communication/communicationServices --query id -o tsv)"
CONTAINER_SCOPE="$STORAGE_ID/blobServices/default/containers/airco-tracker"
table_scope() { printf '%s/tableServices/default/tables/%s\n' "$STORAGE_ID" "$1"; }
secret_scope() { printf '%s/secrets/%s\n' "$VAULT_ID" "$1"; }

# Fail before inspecting/removing RBAC if an application still runs under the
# wrong identity. A secret-scoped role is useful only when bound to its owner.
require_identity_binding app "$WEB_APP_NAME" "$WEB_ID"
require_identity_binding job airco-tracker-job "$SCANNER_ID"
require_identity_binding job airco-alert-retention-job "$RETENTION_ID"
require_identity_binding job "$WEB_RETENTION_JOB_NAME" "$WEB_RETENTION_ID"
require_identity_binding app "$EMAIL_APP_NAME" "$EMAIL_ID"

require_role "$SCANNER_PRINCIPAL" "$ACR_ID" "AcrPull"
require_role "$SCANNER_PRINCIPAL" "$CONTAINER_SCOPE" "Storage Blob Data Contributor"
require_role "$SCANNER_PRINCIPAL" "$(table_scope alertoutbox)" "Storage Table Data Contributor"
require_role "$SCANNER_PRINCIPAL" "$(table_scope alertoutboxpending)" "Storage Table Data Contributor"
for secret in awin-publisher-api-token aliexpress-app-key aliexpress-app-secret; do
  require_role "$SCANNER_PRINCIPAL" "$(secret_scope "$secret")" "Key Vault Secrets User"
done

require_role "$RETENTION_PRINCIPAL" "$ACR_ID" "AcrPull"
for table in alertoutbox alertoutboxpending alertdeliveries alertdeliveryindex alertsuppression; do
  require_role "$RETENTION_PRINCIPAL" "$(table_scope "$table")" "Storage Table Data Contributor"
done
require_role "$RETENTION_PRINCIPAL" "$(table_scope alertrecipients)" "Storage Table Data Reader"
require_role "$WEB_RETENTION_PRINCIPAL" "$ACR_ID" "AcrPull"
for table in users authcodes authsessions; do
  require_role "$WEB_RETENTION_PRINCIPAL" "$(table_scope "$table")" "Storage Table Data Contributor"
done

# Verify every permission that the web runtime must retain before removing a
# single legacy grant. This makes --apply fail safe during RBAC propagation.
require_role "$WEB_PRINCIPAL" "$ACR_ID" "AcrPull"
require_role "$WEB_PRINCIPAL" "$CONTAINER_SCOPE" "Storage Blob Data Reader"
for secret in unsubscribe-signing-key withdrawal-signing-key auth-code-hmac-pepper; do
  require_role "$WEB_PRINCIPAL" "$(secret_scope "$secret")" "Key Vault Secrets User"
done
require_role "$WEB_PRINCIPAL" "$COMMUNICATION_ID" "${PREFIX}-acs-email-sender"
for table in users authcodes authsessions alertrecipients; do
  require_role "$WEB_PRINCIPAL" "$(table_scope "$table")" "Storage Table Data Contributor"
done
require_role "$WEB_PRINCIPAL" "$(table_scope i18n)" "Storage Table Data Reader"

require_role "$EMAIL_PRINCIPAL" "$ACR_ID" "AcrPull"
require_role "$EMAIL_PRINCIPAL" "$(secret_scope unsubscribe-signing-key)" "Key Vault Secrets User"
require_role "$EMAIL_PRINCIPAL" "$COMMUNICATION_ID" "${PREFIX}-acs-email-sender"
for table in alertdeliveries emailratelimit alertdeliveryindex; do
  require_role "$EMAIL_PRINCIPAL" "$(table_scope "$table")" "Storage Table Data Contributor"
done
for table in alertsuppression alertrecipients users alertoutbox i18n; do
  require_role "$EMAIL_PRINCIPAL" "$(table_scope "$table")" "Storage Table Data Reader"
done

# principal|scope|role. The list is intentionally explicit: never delete all
# grants for an identity or at a scope.
bad_grants=(
  "$WEB_PRINCIPAL|$STORAGE_ID|Storage Blob Data Contributor"
  "$WEB_PRINCIPAL|$CONTAINER_SCOPE|Storage Blob Data Contributor"
  "$WEB_PRINCIPAL|$STORAGE_ID|Storage Table Data Contributor"
  "$WEB_PRINCIPAL|$(table_scope alertoutbox)|Storage Table Data Contributor"
  "$WEB_PRINCIPAL|$(table_scope alertoutboxpending)|Storage Table Data Contributor"
  "$WEB_PRINCIPAL|$(table_scope alertdeliveries)|Storage Table Data Contributor"
  "$WEB_PRINCIPAL|$(table_scope alertdeliveryindex)|Storage Table Data Contributor"
  "$WEB_PRINCIPAL|$(table_scope alertsuppression)|Storage Table Data Contributor"
  "$WEB_PRINCIPAL|$COMMUNICATION_ID|Communication and Email Service Owner"
  "$WEB_PRINCIPAL|$VAULT_ID|Key Vault Secrets User"
  "$SCANNER_PRINCIPAL|$VAULT_ID|Key Vault Secrets User"
  "$EMAIL_PRINCIPAL|$VAULT_ID|Key Vault Secrets User"
  "$RETENTION_PRINCIPAL|$(table_scope users)|Storage Table Data Reader"
  "$RETENTION_PRINCIPAL|$(table_scope users)|Storage Table Data Contributor"
  "$RETENTION_PRINCIPAL|$(table_scope authcodes)|Storage Table Data Contributor"
  "$RETENTION_PRINCIPAL|$(table_scope authsessions)|Storage Table Data Contributor"
)

echo "Replacement identities, workload bindings, and exact RBAC scopes verified."
for item in "${bad_grants[@]}"; do
  IFS='|' read -r principal scope role <<< "$item"
  count="$(role_count "$principal" "$scope" "$role")"
  if [[ "$count" -gt 0 ]]; then
    if [[ "$APPLY" == true ]]; then
      az role assignment delete --assignee-object-id "$principal" --scope "$scope" --role "$role"
      echo "Removed legacy grant for $principal: $role at $scope"
    else
      echo "Would remove legacy grant for $principal: $role at $scope"
    fi
  fi
done

if [[ "$APPLY" == false ]]; then
  echo "Dry run complete. Re-run with --apply only after application canaries succeed."
  exit 0
fi

# Re-run the script's verification logic manually after mutation so a partial
# migration cannot be reported as success.
for secret in unsubscribe-signing-key withdrawal-signing-key auth-code-hmac-pepper; do
  require_role "$WEB_PRINCIPAL" "$(secret_scope "$secret")" "Key Vault Secrets User"
done
for secret in awin-publisher-api-token aliexpress-app-key aliexpress-app-secret; do
  require_role "$SCANNER_PRINCIPAL" "$(secret_scope "$secret")" "Key Vault Secrets User"
done
require_role "$EMAIL_PRINCIPAL" "$(secret_scope unsubscribe-signing-key)" "Key Vault Secrets User"

for item in "${bad_grants[@]}"; do
  IFS='|' read -r principal scope role <<< "$item"
  [[ "$(role_count "$principal" "$scope" "$role")" == "0" ]] || {
    echo "Legacy grant still present for $principal: $role at $scope" >&2
    exit 1
  }
done
echo "Least-privilege migration verified."
