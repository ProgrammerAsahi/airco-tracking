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
          └─ Azure Communication Services Email → accepted
                                                       │
ACS recipient delivery reports                        │
  └─ Event Grid system topic → acs-email-delivery-events queue
                                      │
airco-alert-delivery-worker (0–4 replicas)
          ├─ correlates the deterministic ACS message ID
          ├─ records the recipient-level final status
          └─ suppresses the exact address fingerprint after a hard bounce
```

The frontend/auth service is the primary writer of `alertrecipients`. Registration, email/language/country changes, Stripe one-time pass payment/refund webhooks, pass revocation, and account deletion synchronize the projection. `airco-alert-reconciler-job` runs daily at `03:17 UTC` as a repair and legacy-backfill safety net; it is not on the per-event hot path.

`airco-alert-retention-job` runs daily at `02:17 UTC`. The outbox publisher runs every minute, and the scanner remains on `*/10 * * * *` UTC.

### Data and Service Bus entities

- `stock-events` topic / `email-fanout` subscription: one product availability event.
- `email-fanout-jobs` queue: one job per recipient shard.
- `email-jobs` queue: one opaque `eventId` + `recipientId` delivery job.
- `acs-email-delivery-events` queue: dedicated transient ACS recipient reports; unlike the normal queues, its provider-defined body necessarily contains the recipient address.
- `alertoutbox`: durable event payload and publish state, partitioned by event hash prefix.
- `alertoutboxpending`: authoritative enqueue journal in one hot pending partition. Each v2 row contains the complete immutable event and an ETag-guarded recoverable publisher lease; the publisher repairs the archive before acknowledging it. Pointer-only legacy rows are never deleted while their archive may still be racing into existence.
- `alertrecipients`: minimal mail read model in 32 partitions, `r-00` through `r-1f`.
- `alertdeliveries`: idempotency, lease, attempt, terminal status, and ACS operation metadata.
- `alertdeliveryindex`: ACS message ID to opaque delivery binding plus an address fingerprint; no plaintext address.
- `alertsuppression`: recipient-scoped hard-bounce suppression for the exact address fingerprint; no plaintext address.
- `users`: canonical account/pass-entitlement table owned by the web service; the event path does not scan it.

The 32-shard rule is a cross-repository contract. Both the web projection writer and this backend use the low five bits of `sha256(userId)` and `ALERT_RECIPIENT_SHARDS` is deliberately validated as exactly `32`. Changing it requires a versioned, coordinated migration in both repositories.

For the pass migration, deploy the backend version that reads both pass and legacy subscription fields before deploying the web service that writes pass fields. After both revisions are healthy, run reconciliation once to replace remaining projection rows with the new minimal entitlement shape. Reversing this order can temporarily suppress legitimate email recipients.

## Delivery semantics

The pipeline is at-least-once and makes every stage idempotent:

- A real stock event ID is `sha256(event type + country-scoped product key + availability generation)`. Repeated scans of the same available generation cannot create a new logical event.
- Service Bus duplicate detection is enabled for seven days and every message has a deterministic `MessageId`.
- A delivery ID is derived from `eventId + recipientId`. `alertdeliveries` uses ETag-guarded state transitions and a short claim lease, so concurrent workers cannot intentionally send the same delivery.
- ACS receives a deterministic operation ID plus repeatability headers. This narrows the crash window between provider acceptance and the ledger update.
- The email worker records `accepted` after the ACS operation succeeds. Event Grid later advances the ledger to a recipient-level final state: `delivered`, `expanded`, `bounced`, `provider_suppressed`, `quarantined`, `filtered_spam`, or `provider_failed`. ACS acceptance alone is never reported as inbox delivery.
- Before calling ACS, the worker creates the message-ID correlation row. Final reports are idempotent by Event Grid event ID and reject out-of-order older evidence. A hard bounce or provider suppression activates a recipient/address-fingerprint suppression that both send checks enforce; a later delivery for that same fingerprint can clear it. A report for an old address cannot suppress a newly verified address.
- Transient mail failures are rescheduled with backoff; permanent failures are dead-lettered. The application retry budget is five send attempts and Service Bus `maxDeliveryCount` is eight.
- Before each send, the worker point-reads the UUID-keyed canonical `users/id:<uuid>` profile, then repeats that read after any sender-rate wait. For legacy email-keyed profiles, reconciliation stores a private `sourceUserRowKey` in the recipient projection so the worker can point-read the canonical row and strictly re-derive the requested UUID before trusting it. Only projections created before that pointer was backfilled use the bounded `userId` query fallback. A changed email address is therefore authoritative even while the fan-out projection is catching up; an expired, refunded, or revoked pass, deleted account, changed delivery country, or event older than six hours is suppressed rather than sent. Legacy recurring-subscription fields remain read-compatible during the migration window, but any present pass field is authoritative so stale subscription data cannot restore a revoked entitlement.

No distributed system can promise mathematical exactly-once delivery across an external mail provider, but the deterministic provider request and delivery ledger make duplicates unlikely and observable.

## Security and privacy

- Production uses Entra ID and user-assigned Managed Identities. Storage, Service Bus, and ACS local/shared-key authentication is disabled where supported; no connection string or ACS key is stored in the image or GitHub.
- Web, scanner, retention, outbox publisher, fan-out, and email delivery use responsibility-separated identities. New access is scoped to its specific Blob container, Service Bus entity, or Table wherever Azure RBAC permits.
- `stock-events` contains product and delivery-coverage data, but no subscriber data.
- Service Bus fan-out and email messages contain only stable opaque recipient UUIDs. They never contain an email address, nickname, Stripe customer/payment ID, payment method, or card data.
- The separate ACS delivery-report queue is the one narrow PII exception: the Microsoft event schema necessarily contains the recipient address. Its one-day TTL, private seven-day Event Grid dead-letter container, daily Service Bus DLQ purge, dedicated least-privilege identity, and no-body logging bound that exposure. Azure Event Grid validates dead-letter authorization at storage-account scope, so its managed identity has `Storage Blob Data Contributor` on the state account even though the configured endpoint is the dedicated private container; it has no reader role on application tables and no application uses that identity. Never copy a raw provider report into a ticket, normal log, or another queue.
- `alertrecipients` is the only alert-specific table containing email addresses. It contains only the fields required to decide and render a delivery: stable user ID, email, language, delivery country, `entitlementTier` (`alerts` or `radar`), `entitlementStatus`, `entitlementExpiresAt`, enabled flag, synchronization timestamps, and a private canonical source-row pointer used only for legacy point reads. Purchase timestamps and Stripe identifiers are deliberately excluded.
- `alertdeliveries`, `alertdeliveryindex`, and `alertsuppression` contain opaque IDs, delivery state, and pseudonymous address fingerprints, not the destination address. Application logs mask email local parts and final-report logs use only opaque delivery IDs.
- The email worker resolves the current address from the canonical profile immediately before send. Its identity has read-only access to `users`; code consumes only delivery fields. The private source-row pointer is never copied into Service Bus messages, logs, retry metadata, or APIs.
- The private Blob container remains private. Browser access to inventory stays behind the frontend same-origin API and Managed Identity.
- Local `ALERT_DISPATCH_BACKEND=direct` exists for development compatibility. Azure production must use `service_bus` and fails closed when recipient state cannot be checked.

## Retention

- Service Bus stock and fan-out messages: one-day TTL; expired messages are dead-lettered.
- Email jobs: six-hour TTL; production events older than six hours are also suppressed by the application.
- ACS delivery reports: one-day queue TTL and no expiry-to-DLQ. Messages that exhaust delivery attempts are purged from the dedicated Service Bus DLQ by the daily privacy job.
- Event Grid delivery-report dead letters: private Blob container, automatically deleted after seven days.
- Published `alertoutbox` rows: 30 days.
- No-resend/final `alertdeliveries` rows (`accepted`, final provider results, legacy `sent`, business `suppressed`, and `failed`): 90 days. The daily job emits an anonymous warning when any `accepted` row has waited more than two hours for a final report.
- `alertdeliveryindex` correlation rows: 90 days with delivery metadata.
- `alertsuppression` is bounded to one row per recipient/address state. The daily job removes rows whose canonical profile is missing or non-active; it preserves an inconclusive legacy row until reconciliation supplies a canonical source pointer.
- Pending outbox or non-terminal delivery rows are not age-deleted; they must remain available for recovery/investigation.
- The publisher discovers complete events through the single `alertoutboxpending` partition without scanning the sharded archive table. Its legacy migration drains all continuation pages before recording completion. Retention defaults to no row cap with a 240-second runtime budget and warns when backlog remains.
- Log Analytics workspace: 30 days.
- `alertrecipients` follows account lifecycle and is removed when the user deletes the account. The daily reconciler removes stale projection rows only after a complete canonical-user scan.

Change `ALERT_OUTBOX_RETENTION_DAYS` or `ALERT_DELIVERY_RETENTION_DAYS` only after reviewing incident-response and privacy requirements. Run cleanup manually with:

```bash
.venv/bin/python -m airco_tracker cleanup-alert-data --limit 0
```

## Capacity and scaling

The subscriber count is no longer part of scanner latency. A scan writes at most one event per qualifying product transition; independent workers perform recipient expansion and delivery.

- Recipient rows are distributed across 32 Azure Table partitions and streamed in pages of 250. No in-memory list of all subscribers is built.
- Coordinator replicas scale to 4 and fan-out workers to 16 from Service Bus backlog. These values can be raised after measuring Table and Service Bus throttling.
- The Standard-tier topic and all three queues (`email-fanout-jobs`, `email-jobs`, and `acs-email-delivery-events`) are created as 16-partition entities. Alert messages have no global ordering requirement, so partitioning removes the single-broker/entity bottleneck and improves availability. Every batch has one deterministic partition key (stock bucket, event, recipient shard, or provider event), preserving duplicate detection without mixing partition keys in a Service Bus batch. Azure cannot change this flag in place after entity creation; migrate or recreate empty entities before deploying this foundation change.
- `enableServiceBusPartitioning` is a foundation creation/rollback parameter; changing it still requires deleting or versioning the empty entities first because Azure cannot update it in place.
- The canonical `users` table is never scanned per stock event. The daily reconciler streams it only as a repair job, while the email worker performs a constant-time UUID or legacy-source-row point read per actual delivery. The bounded `userId` query remains only as temporary compatibility for projections not yet backfilled with a source pointer. Manual database/table splitting is not currently required for the alert hot path.
- Service Bus is Standard with partition-safe batching and duplicate detection. Monitor namespace throttling and queue age; move to a partitioned Premium namespace when shared-tier latency or the Standard namespace operation ceiling becomes material.
- Production uses the verified customer-managed ACS sender domain `airco-tracker.eu`. The documented default limit is 30 messages/minute and 100/hour. Final-delivery, bounce, suppression, and alert monitoring are operational; the email worker remains intentionally capped at one replica with a 13-second interval until the open higher-quota request is approved. Production reservations are stored in the dedicated `emailratelimit` Table with optimistic ETag writes, so the interval remains global when the worker is later scaled to multiple replicas. Local development defaults to an in-process limiter. `infra/job.bicep` fails closed by forcing one replica whenever the distributed backend is disabled. This provider quota, not Service Bus or Table Storage, is the current end-to-end throughput ceiling.

Domain/SPF/DKIM/DKIM2 verification, Communication Service linking, explicit `ACS_EMAIL_DOMAIN_NAME` selection, and real Gmail/Outlook inbox canaries are complete. Foundation keeps the custom domain alongside the Azure-managed fallback, and deployment selects it by name rather than relying on `linkedDomains` array order. Reply-To, user alert preference, RFC 8058 unsubscribe, recipient-level final-delivery ingestion, hard-bounce suppression, privacy cleanup, and related monitoring are deployed and production-verified. The ACS quota request is open for tier `250` (1,000/minute and 3,000/hour), with its private case identifier kept outside this public repository. Keep `EMAIL_MIN_SEND_INTERVAL_SECONDS=13` and `EMAIL_MAX_REPLICAS=1` until Azure approves it; raising replicas first would only produce ACS `429` responses and queue churn. DNS, consent, final-delivery, suppression, monitoring, quota, and warm-up procedures are in [EMAIL_DELIVERY.md](./EMAIL_DELIVERY.md); custom-domain rollback remains in [ACS_CUSTOM_EMAIL_DOMAIN.md](./ACS_CUSTOM_EMAIL_DOMAIN.md).

Useful capacity signals are active-message count and oldest-message age for all queues/subscriptions, dead-letter count, Service Bus throttled requests/server errors, Event Grid delivery failures/drops/dead letters, accepted-to-final latency, final-status rates, outbox pending age, and ACS `429`/quota responses. Diagnostic Service Bus logs and metrics are sent to Log Analytics. Production has four namespace alerts (`aircontrack-servicebus-deadletter`, `aircontrack-servicebus-backlog`, `aircontrack-servicebus-throttled`, and `aircontrack-servicebus-server-errors`) plus three Event Grid alerts for delivery failure, dropped events, and dead-lettered reports. Two privacy-safe scheduled-query alerts cover accepted deliveries missing a final report after two hours and adverse provider outcomes. Enabled rules bind to the `aircontrack-operations-alerts` Action Group. Outbox-age and ACS-quota-spike alerts, plus continuing end-to-end inbox canaries, remain future hardening work.

## Configuration

`infra/job.bicep` supplies production values. Do not hand-copy credentials into environment variables.

```text
ALERT_DISPATCH_BACKEND=service_bus
SERVICE_BUS_NAMESPACE=<namespace>.servicebus.windows.net
STOCK_EVENTS_TOPIC=stock-events
STOCK_EVENTS_SUBSCRIPTION=email-fanout
FANOUT_JOBS_QUEUE=email-fanout-jobs
EMAIL_JOBS_QUEUE=email-jobs
ACS_DELIVERY_EVENTS_QUEUE=acs-email-delivery-events
AUTH_USERS_TABLE=users
ALERT_OUTBOX_TABLE=alertoutbox
ALERT_OUTBOX_PENDING_TABLE=alertoutboxpending
ALERT_RECIPIENTS_TABLE=alertrecipients
ALERT_DELIVERIES_TABLE=alertdeliveries
ALERT_DELIVERY_INDEX_TABLE=alertdeliveryindex
ALERT_SUPPRESSIONS_TABLE=alertsuppression
ALERT_RECIPIENT_SHARDS=32
ALERT_RECIPIENT_PAGE_SIZE=250
ALERT_EVENT_MAX_AGE_SECONDS=21600
ALERT_OUTBOX_RETENTION_DAYS=30
ALERT_DELIVERY_RETENTION_DAYS=90
STATE_COMPACT_AFTER_DAYS=90
STATE_TOMBSTONE_RETENTION_DAYS=365
SCANNER_LEASE_SECONDS=480
EMAIL_MIN_SEND_INTERVAL_SECONDS=13
EMAIL_RATE_LIMIT_BACKEND=azure_table
EMAIL_RATE_LIMIT_TABLE=emailratelimit
EMAIL_MAX_REPLICAS=1
ACS_EMAIL_DOMAIN_NAME=airco-tracker.eu
EMAIL_REPLY_TO=support@airco-tracker.eu
APP_BASE_URL=https://airco-tracker.eu
EMAIL_UNSUBSCRIBE_SIGNING_KEY=<Key Vault secret reference; never a literal production value>
```

`AZURE_STORAGE_ACCOUNT_URL`, `AZURE_CLIENT_ID`, `ACS_ENDPOINT`, `EMAIL_FROM`, and the normal scanner settings are also supplied by Bicep. `EMAIL_TO` is a local/direct-mode setting and is not a production subscriber source. A local or single-replica worker may keep `EMAIL_RATE_LIMIT_BACKEND=local`; any multi-replica deployment must use `azure_table`.

`operationsAlertEmail` is a secure foundation parameter, not an application environment variable. For initial foundation setup, provide it locally through `AZURE_OPERATIONS_ALERT_EMAIL`; never commit the mailbox or store it as a GitHub Actions variable. On later `deploy-azure.sh` runs, leaving the variable unset makes the script read and preserve the existing `primary-operations-mailbox` receiver from `aircontrack-operations-alerts`. If no receiver has ever been configured, the four metric alerts remain dashboard-visible and enabled but have no email action until the secure parameter is supplied.

## Deployment order

Foundation deployment creates resources and RBAC, so it must be run by an Azure principal allowed to create role assignments. The GitHub deployer intentionally cannot do that.

For a coordinated foundation/RBAC change shared by the backend and web repositories:

```bash
cd ~/airco-tracking
az login
AZURE_FOUNDATION_ONLY=true ./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh

cd ~/airco-tracking-web
./scripts/deploy.sh

cd ~/airco-tracking
./scripts/deploy-application.sh
./scripts/migrate-runtime-identities.sh
# Only after both smoke tests pass:
./scripts/migrate-runtime-identities.sh --apply
```

Foundation-only mode creates resources, identities, RBAC, and the updated least-privilege GitHub role without moving a production workload. The web deploy then creates and immediately verifies its cleanup job before switching traffic. The backend application deploy verifies its candidate and dependency-order jobs. Identity migration is first a read-only audit and never deletes legacy grants automatically; an Owner may use `--apply` only after both smoke tests pass. A full `deploy-azure.sh` remains valid for an already-complete environment; on greenfield bootstrap it explicitly defers the migration audit until the web cleanup job exists. See [runtime hardening and migration](./HARDENING.md). Wait several minutes and retry an application deployment if a newly created role has not propagated yet.

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
PROJECT_DIR="$(pwd)"
RECIPIENT_ID_1='<authorized-recipient-uuid-1>'
RECIPIENT_ID_2='<authorized-recipient-uuid-2>'

RECONCILE_EXECUTION="$(az containerapp job start \
  -g "$RESOURCE_GROUP" -n airco-alert-reconciler-job \
  --query name -o tsv)"
echo "Reconciler execution: $RECONCILE_EXECUTION"
# Wait for this execution to report Succeeded before continuing.

command -v jq >/dev/null || { echo 'jq is required.' >&2; exit 1; }
test -f "$PROJECT_DIR/scripts/render_job_execution_template.py" || {
  echo 'Run this command from the backend repository root.' >&2
  exit 1
}
TEST_YAML="$(mktemp /tmp/airco-pipeline-test.XXXXXX.yaml)"
chmod 600 "$TEST_YAML"
trap 'rm -f "$TEST_YAML"' EXIT

PUBLISHER_IMAGE="$(az containerapp job show \
  -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --query 'properties.template.containers[0].image' -o tsv)"
test -n "$PUBLISHER_IMAGE" || { echo 'Publisher image was empty.' >&2; exit 1; }

az containerapp job show -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --query properties.template -o json \
  | python3 "$PROJECT_DIR/scripts/render_job_execution_template.py" "$PUBLISHER_IMAGE" \
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

Keep the normal scanner and publisher schedules unchanged. Inspect worker logs and broker backlogs without receiving or purging messages; record only event/delivery IDs and counts, never recipient addresses. A successful test requires the targeted execution to succeed, both delivery rows to progress from `accepted` to a recipient-level final status, both inboxes to receive the message, and active/dead-letter counts on the subscription and all three queues to return to zero. Also verify the visible and RFC 8058 unsubscribe paths without changing the paid entitlement. ACS acceptance alone does not prove inbox delivery.

If reconciliation returns no active entitled recipients, stop here and record a safe skip. Do not substitute an expired, deleted, or otherwise ineligible account merely to force the canary. Direct authentication/sender canaries can still verify the custom ACS sender independently; rerun this full pipeline test when an authorized active entitled recipient exists.

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
az containerapp logs show -g airco-tracker-rg -n airco-alert-delivery-worker --follow
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
az servicebus queue show -g airco-tracker-rg \
  --namespace-name <namespace> -n acs-email-delivery-events --query countDetails
```

The daily retention job removes normal metadata; the separate privacy job removes raw final-report messages that reached the dedicated Service Bus DLQ:

```bash
az containerapp job start -g airco-tracker-rg -n airco-delivery-dlq-cleanup
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
