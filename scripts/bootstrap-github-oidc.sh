#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-nl-rg}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-ProgrammerAsahi/airco-tracking}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
EMAIL_LANG="${EMAIL_LANG:-zh}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

if ! az group show --name "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Resource group $RESOURCE_GROUP does not exist." >&2
  echo "Run ./scripts/deploy-azure.sh before bootstrapping CI/CD." >&2
  exit 1
fi

az deployment group create \
  --name airco-github-oidc \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$PROJECT_DIR/infra/github-oidc.bicep" \
  --parameters githubRepository="$GITHUB_REPOSITORY" githubBranch="$GITHUB_BRANCH" \
  --output none

output() {
  az deployment group show \
    --name airco-github-oidc \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.outputs.$1.value" \
    --output tsv
}

AZURE_CLIENT_ID="$(output clientId)"
AZURE_TENANT_ID="$(output tenantId)"
AZURE_SUBSCRIPTION_ID="$(output subscriptionId)"

if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh variable set AZURE_CLIENT_ID --repo "$GITHUB_REPOSITORY" --body "$AZURE_CLIENT_ID"
  gh variable set AZURE_TENANT_ID --repo "$GITHUB_REPOSITORY" --body "$AZURE_TENANT_ID"
  gh variable set AZURE_SUBSCRIPTION_ID --repo "$GITHUB_REPOSITORY" --body "$AZURE_SUBSCRIPTION_ID"
  gh variable set AZURE_RESOURCE_GROUP --repo "$GITHUB_REPOSITORY" --body "$RESOURCE_GROUP"
  gh variable set EMAIL_LANG --repo "$GITHUB_REPOSITORY" --body "$EMAIL_LANG"
  gh variable set KEY_VAULT_SECRET_MAP --repo "$GITHUB_REPOSITORY" --body "EMAIL_TO=notification-email"
  echo "GitHub Actions variables configured for $GITHUB_REPOSITORY."
else
  echo "GitHub CLI is unavailable or not logged in. Add these repository Actions variables manually:"
  echo "AZURE_CLIENT_ID=$AZURE_CLIENT_ID"
  echo "AZURE_TENANT_ID=$AZURE_TENANT_ID"
  echo "AZURE_SUBSCRIPTION_ID=$AZURE_SUBSCRIPTION_ID"
  echo "AZURE_RESOURCE_GROUP=$RESOURCE_GROUP"
  echo "EMAIL_LANG=$EMAIL_LANG"
  echo "KEY_VAULT_SECRET_MAP=EMAIL_TO=notification-email"
fi

echo "OIDC trust is restricted to $GITHUB_REPOSITORY on branch $GITHUB_BRANCH."
