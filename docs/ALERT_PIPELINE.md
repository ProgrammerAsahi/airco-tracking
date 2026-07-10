# Airco Tracker — asynchronous alert pipeline

<p align="center">
  <a href="./ALERT_PIPELINE.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/ALERT_PIPELINE-简体中文-d73a49"></a>
  <a href="./ALERT_PIPELINE.md"><img alt="English" src="https://img.shields.io/badge/ALERT_PIPELINE-English-0969da"></a>
</p>

This document is the operational and security reference for the production stock-alert path. The scanner and inventory snapshot remain independent from mail delivery: a slow or unavailable mail provider must not slow retailer scans or make the dashboard stale.

## Architecture

```text
airco-tracker-job (every 10 minutes, one distributed scanner lease)
  ├─ writes private state.json and inventory.json
  └─ writes one deterministic stock.available.v1 row to alertoutbox
          │
airco-alert-publisher-job (every minute)
          └─ stock-events topic → email-fanout subscription
                                      │
airco-alert-fanout-coordinator (0–4 replicas)
          └─ 32 jobs → email-fanout-jobs queue
                                      │
airco-alert-fanout-worker (0–16 replicas)
          ├─ streams one alertrecipients shard at a time
          ├─ checks current email entitlement and delivery country
          └─ opaque recipient UUID jobs → email-jobs queue
                                      │
airco-alert-email-worker (currently 0–1 replica)
          ├─ point-reads the UUID-keyed canonical user immediately before sending
          ├─ claims event × recipient in alertdeliveries
          └─ Azure Communication Services Email
```

The frontend/auth service is the primary writer of `alertrecipients`. Registration, email/language/country changes, Stripe subscription webhooks, cancellation, and account deletion synchronize the projection. `airco-alert-reconciler-job` runs daily at `03:17 UTC` as a repair and legacy-backfill safety net; it is not on the per-event hot path.

`airco-alert-retention-job` runs daily at `02:17 UTC`. The outbox publisher runs every minute, and the scanner remains on `*/10 * * * *` UTC.

### Data and Service Bus entities

- `stock-events` topic / `email-fanout` subscription: one product availability event.
- `email-fanout-jobs` queue: one job per recipient shard.
- `email-jobs` queue: one opaque `eventId` + `recipientId` delivery job.
- `alertoutbox`: durable event payload and publish state, partitioned by event hash prefix.
- `alertrecipients`: minimal mail read model in 32 partitions, `r-00` through `r-1f`.
- `alertdeliveries`: idempotency, lease, attempt, terminal status, and ACS operation metadata.
- `users`: canonical account/subscription table owned by the web service; the event path does not scan it.

The 32-shard rule is a cross-repository contract. Both the web projection writer and this backend use the low five bits of `sha256(userId)` and `ALERT_RECIPIENT_SHARDS` is deliberately validated as exactly `32`. Changing it requires a versioned, coordinated migration in both repositories.

## Delivery semantics

The pipeline is at-least-once and makes every stage idempotent:

- A real stock event ID is `sha256(event type + country-scoped product key + availability generation)`. Repeated scans of the same available generation cannot create a new logical event.
- Service Bus duplicate detection is enabled for seven days and every message has a deterministic `MessageId`.
- A delivery ID is derived from `eventId + recipientId`. `alertdeliveries` uses ETag-guarded state transitions and a short claim lease, so concurrent workers cannot intentionally send the same delivery.
- ACS receives a deterministic operation ID plus repeatability headers. This narrows the crash window between provider acceptance and the ledger update.
- Transient mail failures are rescheduled with backoff; permanent failures are dead-lettered. The application retry budget is five send attempts and Service Bus `maxDeliveryCount` is eight.
- Before each send, the worker point-reads the UUID-keyed canonical `users/id:<uuid>` profile, then repeats that read after any sender-rate wait. For legacy email-keyed profiles, reconciliation stores a private `sourceUserRowKey` in the recipient projection so the worker can point-read the canonical row and strictly re-derive the requested UUID before trusting it. Only projections created before that pointer was backfilled use the bounded `userId` query fallback. A changed email address is therefore authoritative even while the fan-out projection is catching up; an expired/cancelled entitlement, deleted account, changed delivery country, or event older than six hours is suppressed rather than sent.

No distributed system can promise mathematical exactly-once delivery across an external mail provider, but the deterministic provider request and delivery ledger make duplicates unlikely and observable.

## Security and privacy

- Production uses Entra ID and user-assigned Managed Identities. Storage, Service Bus, and ACS local/shared-key authentication is disabled where supported; no connection string or ACS key is stored in the image or GitHub.
- Scanner/shared web runtime, outbox publisher, fan-out, and email delivery use separate identities. New pipeline access is scoped to its specific Service Bus entity or Table wherever Azure RBAC permits; do not merge the workers into one broad Contributor identity.
- `stock-events` contains product and delivery-coverage data, but no subscriber data.
- Service Bus fan-out and email messages contain only stable opaque recipient UUIDs. They never contain an email address, nickname, Stripe customer/subscription ID, payment method, or card data.
- `alertrecipients` is the only alert-specific table containing email addresses. It contains only the fields required to decide and render a delivery: stable user ID, email, language, delivery country, plan/status/end time, enabled flag, synchronization timestamps, and a private canonical source-row pointer used only for legacy point reads.
- `alertdeliveries` contains opaque IDs and delivery status, not the destination address. Application logs mask email local parts.
- The email worker resolves the current address from the canonical profile immediately before send. Its identity has read-only access to `users`; code consumes only delivery fields. The private source-row pointer is never copied into Service Bus messages, logs, retry metadata, or APIs.
- The private Blob container remains private. Browser access to inventory stays behind the frontend same-origin API and Managed Identity.
- Local `ALERT_DISPATCH_BACKEND=direct` exists for development compatibility. Azure production must use `service_bus` and fails closed when recipient state cannot be checked.

## Retention

- Service Bus stock and fan-out messages: one-day TTL; expired messages are dead-lettered.
- Email jobs: six-hour TTL; production events older than six hours are also suppressed by the application.
- Published `alertoutbox` rows: 30 days.
- Terminal `alertdeliveries` rows (`sent`, `suppressed`, `failed`): 90 days.
- Pending outbox or non-terminal delivery rows are not age-deleted; they must remain available for recovery/investigation.
- Log Analytics workspace: 30 days.
- `alertrecipients` follows account lifecycle and is removed when the user deletes the account. The daily reconciler removes stale projection rows only after a complete canonical-user scan.

Change `ALERT_OUTBOX_RETENTION_DAYS` or `ALERT_DELIVERY_RETENTION_DAYS` only after reviewing incident-response and privacy requirements. Run cleanup manually with:

```bash
.venv/bin/python -m airco_tracker cleanup-alert-data --limit 5000
```

## Capacity and scaling

The subscriber count is no longer part of scanner latency. A scan writes at most one event per qualifying product transition; independent workers perform recipient expansion and delivery.

- Recipient rows are distributed across 32 Azure Table partitions and streamed in pages of 250. No in-memory list of all subscribers is built.
- Coordinator replicas scale to 4 and fan-out workers to 16 from Service Bus backlog. These values can be raised after measuring Table and Service Bus throttling.
- The Standard-tier topic and both queues are created as 16-partition entities. Alert messages have no global ordering requirement, so partitioning removes the single-broker/entity bottleneck and improves availability. Every batch has one deterministic partition key (stock bucket, event, or recipient shard), preserving duplicate detection without mixing partition keys in a Service Bus batch. Azure cannot change this flag in place after entity creation; migrate or recreate empty entities before deploying this foundation change.
- `enableServiceBusPartitioning` is a foundation creation/rollback parameter; changing it still requires deleting or versioning the empty entities first because Azure cannot update it in place.
- The canonical `users` table is never scanned per stock event. The daily reconciler streams it only as a repair job, while the email worker performs a constant-time UUID or legacy-source-row point read per actual delivery. The bounded `userId` query remains only as temporary compatibility for projections not yet backfilled with a source pointer. Manual database/table splitting is not currently required for the alert hot path.
- Service Bus is Standard with partition-safe batching and duplicate detection. Monitor namespace throttling and queue age; move to a partitioned Premium namespace when shared-tier latency or the Standard namespace operation ceiling becomes material.
- The email worker is intentionally capped at one replica with a 13-second process-local interval while using an Azure-managed ACS sender domain. That domain is limited to approximately 5 messages/minute and 10 messages/hour. This is the current end-to-end throughput ceiling, not Service Bus or Table Storage.

Before production growth, verify a customer-managed sender domain (for example `airco-tracker.eu`) in ACS, complete SPF/DKIM DNS verification, request an ACS quota increase, and then adjust both `EMAIL_MIN_SEND_INTERVAL_SECONDS` and `EMAIL_MAX_REPLICAS`. Set `ACS_EMAIL_DOMAIN_NAME` to the verified domain only after it is linked. Foundation accepts `customEmailDomainId` and keeps that domain alongside the Azure-managed fallback; the deployment script selects by domain name rather than relying on `linkedDomains` array order. Do not raise replicas first: it would only produce ACS `429` responses and queue churn. The exact DNS, verification, linking, canary, quota, and rollback procedure is in [ACS_CUSTOM_EMAIL_DOMAIN.md](./ACS_CUSTOM_EMAIL_DOMAIN.md).

Useful capacity signals are active-message count and oldest-message age for both queues/subscription, dead-letter count, Service Bus throttled requests/server errors, outbox pending age, delivery failure rate, and ACS `429`/quota responses. Diagnostic Service Bus logs and metrics are sent to Log Analytics. Foundation also creates and enables four namespace metric alerts: `aircontrack-servicebus-deadletter`, `aircontrack-servicebus-backlog`, `aircontrack-servicebus-throttled`, and `aircontrack-servicebus-server-errors`. In the deployed environment they are bound to the enabled `aircontrack-operations-alerts` Action Group. Alerts for outbox age, delivery failure spikes, ACS quota responses, and end-to-end inbox delivery still require separate instrumentation or rules.

## Configuration

`infra/job.bicep` supplies production values. Do not hand-copy credentials into environment variables.

```text
ALERT_DISPATCH_BACKEND=service_bus
SERVICE_BUS_NAMESPACE=<namespace>.servicebus.windows.net
STOCK_EVENTS_TOPIC=stock-events
STOCK_EVENTS_SUBSCRIPTION=email-fanout
FANOUT_JOBS_QUEUE=email-fanout-jobs
EMAIL_JOBS_QUEUE=email-jobs
AUTH_USERS_TABLE=users
ALERT_OUTBOX_TABLE=alertoutbox
ALERT_RECIPIENTS_TABLE=alertrecipients
ALERT_DELIVERIES_TABLE=alertdeliveries
ALERT_RECIPIENT_SHARDS=32
ALERT_RECIPIENT_PAGE_SIZE=250
ALERT_EVENT_MAX_AGE_SECONDS=21600
ALERT_OUTBOX_RETENTION_DAYS=30
ALERT_DELIVERY_RETENTION_DAYS=90
SCANNER_LEASE_SECONDS=480
EMAIL_MIN_SEND_INTERVAL_SECONDS=13
EMAIL_MAX_REPLICAS=1
ACS_EMAIL_DOMAIN_NAME=AzureManagedDomain
```

`AZURE_STORAGE_ACCOUNT_URL`, `AZURE_CLIENT_ID`, `ACS_ENDPOINT`, `EMAIL_FROM`, and the normal scanner settings are also supplied by Bicep. `EMAIL_TO` is a local/direct-mode setting and is not a production subscriber source.

`operationsAlertEmail` is a secure foundation parameter, not an application environment variable. For initial foundation setup, provide it locally through `AZURE_OPERATIONS_ALERT_EMAIL`; never commit the mailbox or store it as a GitHub Actions variable. On later `deploy-azure.sh` runs, leaving the variable unset makes the script read and preserve the existing `primary-operations-mailbox` receiver from `aircontrack-operations-alerts`. If no receiver has ever been configured, the four metric alerts remain dashboard-visible and enabled but have no email action until the secure parameter is supplied.

## Deployment order

Foundation deployment creates resources and RBAC, so it must be run by an Azure principal allowed to create role assignments. The GitHub deployer intentionally cannot do that.

For a new environment or a foundation/RBAC change:

```bash
cd ~/airco-tracking
az login
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

`deploy-azure.sh` registers providers, deploys `infra/foundation.bicep`, builds the image, deploys `infra/job.bicep`, and starts dependency-order verification jobs. Wait several minutes and retry the application deployment if a newly created role has not propagated yet.

For the first operations receiver configuration, set `AZURE_OPERATIONS_ALERT_EMAIL` only in the local environment that runs `deploy-azure.sh`, then unset it after deployment. ARM treats the mapped `operationsAlertEmail` parameter as secure, and subsequent runs preserve the existing receiver without requiring the address again.

For ordinary application-only releases, a push to `main` runs tests, builds an immutable commit-SHA image, deploys jobs/apps, then verifies recipient reconciliation, scanner execution, and outbox publication. Markdown-only changes do not trigger deployment. A manual application deployment is:

```bash
IMAGE_TAG="$(git rev-parse --short=12 HEAD)" \
AZURE_RESOURCE_GROUP=airco-tracker-rg \
./scripts/deploy-application.sh
```

After a foundation change, rerun `bootstrap-github-oidc.sh` before depending on GitHub Actions so the least-privilege custom deployer role includes the current resource types and actions.

## Verification and targeted email test

Local checks that do not send production mail:

```bash
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
az bicep build --file infra/foundation.bicep --stdout >/dev/null
az bicep build --file infra/job.bicep --stdout >/dev/null
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

The real-mail synthetic test is intentionally targeted and must run through the already deployed publisher Managed Identity. Do **not** grant a developer/personal Azure principal temporary Table, Service Bus, or ACS data-plane roles, and do not put email addresses in the command, YAML, logs, or Service Bus. Obtain the exact stable recipient UUIDs through an authorized account/reconciliation workflow and use `--recipient-id` only.

First run the Managed-Identity reconciler and confirm that execution succeeds. Then create a mode-`0600` one-time execution template from the currently deployed publisher template, replace only its command arguments with the authorized opaque UUIDs, and start it. `job start --yaml` starts one execution; it does not change the saved image, schedule, identity, or normal publisher arguments:

```bash
RESOURCE_GROUP=airco-tracker-rg
PUBLISHER_JOB=airco-alert-publisher-job
RECIPIENT_ID_1='<authorized-recipient-uuid-1>'
RECIPIENT_ID_2='<authorized-recipient-uuid-2>'

RECONCILE_EXECUTION="$(az containerapp job start \
  -g "$RESOURCE_GROUP" -n airco-alert-reconciler-job \
  --query name -o tsv)"
echo "Reconciler execution: $RECONCILE_EXECUTION"
# Wait for this execution to report Succeeded before continuing.

command -v jq >/dev/null || { echo 'jq is required.' >&2; exit 1; }
TEST_YAML="$(mktemp /tmp/airco-pipeline-test.XXXXXX.yaml)"
chmod 600 "$TEST_YAML"
trap 'rm -f "$TEST_YAML"' EXIT

az containerapp job show -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --query properties.template -o json \
  | jq --arg first "$RECIPIENT_ID_1" --arg second "$RECIPIENT_ID_2" '
      .containers[0].command = ["airco-tracker"]
      | .containers[0].args = [
          "pipeline-test",
          "--recipient-id", $first,
          "--recipient-id", $second
        ]
    ' > "$TEST_YAML"

TEST_EXECUTION="$(az containerapp job start \
  -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --yaml "$TEST_YAML" --query name -o tsv)"
rm -f "$TEST_YAML"
trap - EXIT
echo "Targeted test execution: $TEST_EXECUTION"

az containerapp job logs show -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --execution "$TEST_EXECUTION" --container outbox-publisher \
  --tail 50 --format text
```

Keep the normal scanner and publisher schedules unchanged. Inspect the email-worker logs and the three broker backlogs without receiving or purging messages; record only event/delivery IDs and counts, never recipient addresses. A successful test requires the targeted execution to succeed, both delivery rows to reach terminal `sent`/ACS-accepted handling, both inboxes to receive the message, and active/dead-letter counts on the subscription and both queues to return to zero. ACS acceptance alone does not prove inbox delivery.

## Operations

Start repair and cleanup jobs manually:

```bash
az containerapp job start -g airco-tracker-rg -n airco-alert-reconciler-job
az containerapp job start -g airco-tracker-rg -n airco-alert-retention-job
```

Inspect job executions and worker logs:

```bash
az containerapp job execution list -g airco-tracker-rg -n airco-tracker-job -o table
az containerapp job logs show -g airco-tracker-rg -n airco-tracker-job --follow
az containerapp logs show -g airco-tracker-rg -n airco-alert-fanout-worker --follow
az containerapp logs show -g airco-tracker-rg -n airco-alert-email-worker --follow
```

Inspect Service Bus backlog without receiving messages:

```bash
az servicebus topic subscription show -g airco-tracker-rg \
  --namespace-name <namespace> --topic-name stock-events -n email-fanout \
  --query countDetails
az servicebus queue show -g airco-tracker-rg \
  --namespace-name <namespace> -n email-fanout-jobs --query countDetails
az servicebus queue show -g airco-tracker-rg \
  --namespace-name <namespace> -n email-jobs --query countDetails
```

Inspect the four enabled alert rules and their Action Group without reading or changing the receiver address:

```bash
az monitor metrics alert list -g airco-tracker-rg \
  --query "[?starts_with(name, 'aircontrack-servicebus-')].{name:name,enabled:enabled}" \
  -o table
az monitor action-group show -g airco-tracker-rg \
  -n aircontrack-operations-alerts \
  --query "{name:name,enabled:enabled,receiverCount:length(emailReceivers)}"
```

Do not purge or replay a dead-letter queue blindly. Record the dead-letter reason, fix permanent payload/configuration errors first, and replay only when the deterministic event/delivery IDs make the action safe. A sustained backlog with no dead letters usually indicates capacity or quota pressure; a growing dead-letter count indicates invalid payloads or permanent delivery failures.
