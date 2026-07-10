#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
PREFIX="${AZURE_PREFIX:-aircontrack}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-ProgrammerAsahi/airco-tracking}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
EMAIL_LANG="${EMAIL_LANG:-zh}"
EMAIL_MIN_SEND_INTERVAL_SECONDS="${EMAIL_MIN_SEND_INTERVAL_SECONDS:-13}"
EMAIL_MAX_REPLICAS="${EMAIL_MAX_REPLICAS:-1}"
ACS_EMAIL_DOMAIN_NAME="${ACS_EMAIL_DOMAIN_NAME:-AzureManagedDomain}"
DEPLOYMENT_PAUSED="${DEPLOYMENT_PAUSED:-false}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

if ! az group show --name "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Resource group $RESOURCE_GROUP does not exist." >&2
  echo "Run ./scripts/deploy-azure.sh before bootstrapping CI/CD." >&2
  exit 1
fi

OIDC_PARAMETERS=(githubRepository="$GITHUB_REPOSITORY" githubBranch="$GITHUB_BRANCH")
EXISTING_DEPLOYER_PRINCIPAL="$(
  az identity show \
    --name airco-github-deployer \
    --resource-group "$RESOURCE_GROUP" \
    --query principalId \
    --output tsv 2>/dev/null || true
)"
if [[ -n "$EXISTING_DEPLOYER_PRINCIPAL" ]]; then
  EXISTING_DEPLOYER_ASSIGNMENTS="$(
    az role assignment list \
      --assignee-object-id "$EXISTING_DEPLOYER_PRINCIPAL" \
      --resource-group "$RESOURCE_GROUP" \
      --query "[?roleDefinitionName=='Airco GitHub Deployer Minimal'] | length(@)" \
      --output tsv
  )"
  if [[ "$EXISTING_DEPLOYER_ASSIGNMENTS" -gt 0 ]]; then
    OIDC_PARAMETERS+=(manageRoleAssignment=false)
  fi
fi

az deployment group create \
  --name airco-github-oidc \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$PROJECT_DIR/infra/github-oidc.bicep" \
  --parameters "${OIDC_PARAMETERS[@]}" \
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
  gh variable set AZURE_PREFIX --repo "$GITHUB_REPOSITORY" --body "$PREFIX"
  gh variable set EMAIL_LANG --repo "$GITHUB_REPOSITORY" --body "$EMAIL_LANG"
  gh variable set EMAIL_MIN_SEND_INTERVAL_SECONDS --repo "$GITHUB_REPOSITORY" --body "$EMAIL_MIN_SEND_INTERVAL_SECONDS"
  gh variable set EMAIL_MAX_REPLICAS --repo "$GITHUB_REPOSITORY" --body "$EMAIL_MAX_REPLICAS"
  gh variable set ACS_EMAIL_DOMAIN_NAME --repo "$GITHUB_REPOSITORY" --body "$ACS_EMAIL_DOMAIN_NAME"
  gh variable set DEPLOYMENT_PAUSED --repo "$GITHUB_REPOSITORY" --body "$DEPLOYMENT_PAUSED"
  echo "GitHub Actions variables configured for $GITHUB_REPOSITORY."
else
  echo "GitHub CLI is unavailable or not logged in. Add these repository Actions variables manually:"
  echo "AZURE_CLIENT_ID=$AZURE_CLIENT_ID"
  echo "AZURE_TENANT_ID=$AZURE_TENANT_ID"
  echo "AZURE_SUBSCRIPTION_ID=$AZURE_SUBSCRIPTION_ID"
  echo "AZURE_RESOURCE_GROUP=$RESOURCE_GROUP"
  echo "AZURE_PREFIX=$PREFIX"
  echo "EMAIL_LANG=$EMAIL_LANG"
  echo "EMAIL_MIN_SEND_INTERVAL_SECONDS=$EMAIL_MIN_SEND_INTERVAL_SECONDS"
  echo "EMAIL_MAX_REPLICAS=$EMAIL_MAX_REPLICAS"
  echo "ACS_EMAIL_DOMAIN_NAME=$ACS_EMAIL_DOMAIN_NAME"
  echo "DEPLOYMENT_PAUSED=$DEPLOYMENT_PAUSED"
fi

echo "OIDC trust is restricted to $GITHUB_REPOSITORY on branch $GITHUB_BRANCH."
