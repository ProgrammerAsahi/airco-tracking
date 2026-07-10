#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-airco-tracker-rg}"
DOMAIN_NAME="${ACS_CUSTOM_EMAIL_DOMAIN_NAME:-airco-tracker.eu}"
BACKEND_REPOSITORY="${BACKEND_GITHUB_REPOSITORY:-ProgrammerAsahi/airco-tracking}"
FRONTEND_REPOSITORY="${FRONTEND_GITHUB_REPOSITORY:-ProgrammerAsahi/airco-tracking-web}"

case "$ACTION" in
  prepare|records|verify|status|link|configure-github) ;;
  *)
    echo "Usage: $0 {prepare|records|verify|status|link|configure-github}" >&2
    exit 2
    ;;
esac

if [[ ! "$DOMAIN_NAME" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "ACS_CUSTOM_EMAIL_DOMAIN_NAME is not a valid DNS domain name." >&2
  exit 2
fi
if [[ ! "$BACKEND_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "BACKEND_GITHUB_REPOSITORY must use owner/repository format." >&2
  exit 2
fi
if [[ ! "$FRONTEND_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "FRONTEND_GITHUB_REPOSITORY must use owner/repository format." >&2
  exit 2
fi

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required." >&2; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

single_resource_name() {
  local configured="$1"
  local resource_type="$2"
  if [[ -n "$configured" ]]; then
    printf '%s\n' "$configured"
    return
  fi
  local names count
  names="$(az resource list --resource-group "$RESOURCE_GROUP" --resource-type "$resource_type" --query '[].name' --output tsv)"
  count="$(printf '%s\n' "$names" | awk 'NF { count++ } END { print count + 0 }')"
  if [[ "$count" != "1" ]]; then
    echo "Expected exactly one $resource_type in $RESOURCE_GROUP; found $count." >&2
    return 1
  fi
  printf '%s\n' "$names" | awk 'NF { print; exit }'
}

EMAIL_SERVICE_NAME="$(single_resource_name "${EMAIL_SERVICE_NAME:-}" Microsoft.Communication/emailServices)"
COMMUNICATION_SERVICE_NAME="$(single_resource_name "${COMMUNICATION_SERVICE_NAME:-}" Microsoft.Communication/communicationServices)"

domain_list_json() {
  az communication email domain list \
    --resource-group "$RESOURCE_GROUP" \
    --email-service-name "$EMAIL_SERVICE_NAME" \
    --only-show-errors \
    --output json
}

find_domain_json() {
  domain_list_json | jq --arg name "$DOMAIN_NAME" '
    [.[] | select(.name == $name)]
    | if length == 0 then empty
      elif length == 1 then .[0]
      else error("duplicate ACS email domains")
      end
  '
}

require_domain_json() {
  local domain
  domain="$(find_domain_json)"
  if [[ -z "$domain" ]]; then
    echo "$DOMAIN_NAME does not exist. Run '$0 prepare' first." >&2
    return 1
  fi
  printf '%s\n' "$domain"
}

communication_json() {
  az communication show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$COMMUNICATION_SERVICE_NAME" \
    --only-show-errors \
    --output json
}

unverified_requirements() {
  jq -r '
    ["Domain", "SPF", "DKIM", "DKIM2"] as $required
    | [
        $required[] as $kind
        | (.verificationStates[$kind].status // "Missing") as $status
        | select($status != "Verified")
        | "\($kind)=\($status)"
      ]
    | join(", ")
  '
}

assert_domain_verified() {
  local domain="$1"
  local provisioning_state unverified
  provisioning_state="$(jq -r '.provisioningState // "Missing"' <<< "$domain")"
  if [[ "$provisioning_state" != "Succeeded" ]]; then
    echo "Cannot use $DOMAIN_NAME; provisioningState=$provisioning_state." >&2
    return 1
  fi
  unverified="$(unverified_requirements <<< "$domain")"
  if [[ -n "$unverified" ]]; then
    echo "Cannot use $DOMAIN_NAME; required verification is incomplete: $unverified" >&2
    return 1
  fi
}

is_domain_linked() {
  local domain_id="$1"
  communication_json | jq --arg domain_id "$domain_id" '
    (.linkedDomains // []) | index($domain_id) != null
  '
}

assert_domain_linked() {
  local domain="$1"
  local domain_id linked
  domain_id="$(jq -er '.id | select(type == "string" and length > 0)' <<< "$domain")"
  linked="$(is_domain_linked "$domain_id")"
  if [[ "$linked" != "true" ]]; then
    echo "Cannot configure $DOMAIN_NAME for deployment; run '$0 link' first." >&2
    return 1
  fi
}

show_records() {
  local domain
  domain="$(require_domain_json)"
  printf 'Purpose\tType\tHost\tValue\tTTL\n'
  jq -r '
    (.verificationRecords // {})
    | to_entries[]
    | [.key, .value.type, .value.name, .value.value, (.value.ttl | tostring)]
    | @tsv
  ' <<< "$domain"
  echo "DMARC is intentionally deferred until aggregate-report monitoring is configured; it is not required for ACS domain linking." >&2
}

show_status() {
  local domain domain_id linked
  domain="$(find_domain_json)"
  if [[ -z "$domain" ]]; then
    jq -n --arg domain "$DOMAIN_NAME" '{
      exists: false,
      domain: $domain,
      linked: false,
      nextAction: "prepare"
    }'
    return
  fi
  domain_id="$(jq -er '.id | select(type == "string" and length > 0)' <<< "$domain")"
  linked="$(is_domain_linked "$domain_id")"
  jq --argjson linked "$linked" '{
    exists: true,
    domain: .name,
    domainManagement,
    provisioningState,
    fromSenderDomain,
    verificationStates,
    linked: $linked
  }' <<< "$domain"
}

case "$ACTION" in
  prepare)
    domain="$(find_domain_json)"
    if [[ -z "$domain" ]]; then
      az communication email domain create \
        --resource-group "$RESOURCE_GROUP" \
        --email-service-name "$EMAIL_SERVICE_NAME" \
        --name "$DOMAIN_NAME" \
        --location global \
        --domain-management CustomerManaged \
        --only-show-errors \
        --output none
    elif [[ "$(jq -r '.domainManagement // "Missing"' <<< "$domain")" != "CustomerManaged" ]]; then
      echo "$DOMAIN_NAME exists but is not a CustomerManaged domain." >&2
      exit 1
    fi
    show_records
    ;;
  records)
    show_records
    ;;
  verify)
    domain="$(require_domain_json)"
    for verification_type in Domain SPF DKIM DKIM2; do
      verification_status="$(
        jq -r --arg kind "$verification_type" \
          '.verificationStates[$kind].status // "Missing"' <<< "$domain"
      )"
      case "$verification_status" in
        Verified)
          printf '%s verification is already complete; skipping.\n' "$verification_type" >&2
          ;;
        VerificationRequested|VerificationInProgress)
          printf '%s verification is already pending (%s); skipping.\n' \
            "$verification_type" "$verification_status" >&2
          ;;
        NotStarted|VerificationFailed)
          az communication email domain initiate-verification \
            --resource-group "$RESOURCE_GROUP" \
            --email-service-name "$EMAIL_SERVICE_NAME" \
            --domain-name "$DOMAIN_NAME" \
            --verification-type "$verification_type" \
            --only-show-errors \
            --output none
          ;;
        *)
          echo "Refusing to alter unknown $verification_type verification status: $verification_status" >&2
          exit 1
          ;;
      esac
    done
    show_status
    ;;
  status)
    show_status
    ;;
  link)
    domain="$(require_domain_json)"
    assert_domain_verified "$domain"
    custom_domain_id="$(jq -er '.id | select(type == "string" and length > 0)' <<< "$domain")"
    if [[ "$(is_domain_linked "$custom_domain_id")" == "true" ]]; then
      echo "$DOMAIN_NAME is already linked; skipping." >&2
      show_status
      exit 0
    fi
    communication="$(communication_json)"
    linked_domains=()
    while IFS= read -r linked_domain; do
      [[ -n "$linked_domain" ]] && linked_domains+=("$linked_domain")
    done < <(
      jq -r --arg custom_domain_id "$custom_domain_id" \
        '((.linkedDomains // []) + [$custom_domain_id]) | unique[]' <<< "$communication"
    )
    az communication update \
      --resource-group "$RESOURCE_GROUP" \
      --name "$COMMUNICATION_SERVICE_NAME" \
      --linked-domains "${linked_domains[@]}" \
      --only-show-errors \
      --output none
    if [[ "$(is_domain_linked "$custom_domain_id")" != "true" ]]; then
      echo "ACS accepted the update but $DOMAIN_NAME is not linked." >&2
      exit 1
    fi
    show_status
    ;;
  configure-github)
    command -v gh >/dev/null || { echo "GitHub CLI (gh) is required." >&2; exit 1; }
    gh auth status >/dev/null || { echo "Run 'gh auth login' first." >&2; exit 1; }
    domain="$(require_domain_json)"
    assert_domain_verified "$domain"
    assert_domain_linked "$domain"
    gh variable set ACS_EMAIL_DOMAIN_NAME --repo "$BACKEND_REPOSITORY" --body "$DOMAIN_NAME"
    gh variable set ACS_EMAIL_DOMAIN_NAME --repo "$FRONTEND_REPOSITORY" --body "$DOMAIN_NAME"
    printf 'ACS_EMAIL_DOMAIN_NAME=%s configured for %s and %s.\n' \
      "$DOMAIN_NAME" "$BACKEND_REPOSITORY" "$FRONTEND_REPOSITORY"
    ;;
esac
