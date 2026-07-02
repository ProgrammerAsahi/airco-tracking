#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-nl-rg}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-ProgrammerAsahi/airco-tracking-nl}"
EMAIL_LANG="${EMAIL_LANG:-zh}"
EMAIL_TO="${EMAIL_TO:-}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
command -v gh >/dev/null || { echo "GitHub CLI (gh) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first." >&2; exit 1; }

if [[ -z "$EMAIL_TO" ]]; then
  EMAIL_TO="$(gh variable get EMAIL_TO --repo "$GITHUB_REPOSITORY" 2>/dev/null || true)"
fi
if [[ -z "$EMAIL_TO" ]]; then
  read -r -s -p "Notification email address: " EMAIL_TO
  echo
fi
if [[ -z "$EMAIL_TO" || "$EMAIL_TO" != *@* ]]; then
  echo "A valid notification email address is required." >&2
  exit 1
fi

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

gh variable set KEY_VAULT_SECRET_MAP \
  --repo "$GITHUB_REPOSITORY" \
  --body "EMAIL_TO=notification-email"
gh variable set EMAIL_LANG --repo "$GITHUB_REPOSITORY" --body "$EMAIL_LANG"
gh variable delete EMAIL_TO --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1 || true

echo "Notification email stored in Azure Key Vault."
echo "GitHub Actions now stores only the Key Vault mapping and EMAIL_LANG=$EMAIL_LANG."
