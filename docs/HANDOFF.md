# Airco Tracker — current handoff

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

Last updated: 2026-07-09 (Europe/Amsterdam)

Update this English file and `HANDOFF.zh.md` together. Do not record secrets, email addresses, access tokens, payment data, or unnecessary personal information.

## Current objective

Operate a reliable portable-air-conditioner tracker for delivery to Dutch and French addresses, with a country-oriented design ready for more European markets. The scanner runs every ten minutes, keeps the private inventory snapshot current, and produces a stock event only for a first-seen or newly restocked immediate product that passes alert filters.

The current change replaces synchronous per-user sending with an Azure Service Bus Standard pipeline. Subscriber growth must not increase retailer-scan latency, and mail-provider failures must not prevent inventory/state progress.

## Repository and production

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking`
- Branch/local path: `main`, `~/airco-tracking`
- Resource group: `airco-tracker-rg`
- Frontend/auth repository: `https://github.com/ProgrammerAsahi/airco-tracking-web`
- Public site: `https://airco-tracker.eu/`
- Private inventory contract: Blob `airco-tracker/inventory.json`, schema version `1`
- Scanner job: `airco-tracker-job`, `*/10 * * * *` UTC
- Production mail provider: Azure Communication Services Email
- Documentation-only pushes are ignored by the deployment workflow.

At this handoff snapshot the Service Bus implementation is in the coordinated backend/frontend worktrees. Do not mark it released until the exact immutable image SHA, GitHub run, Azure verification executions, targeted delivery event ID/status, and queue/DLQ counts have been recorded here after rollout.

## Asynchronous alert pipeline

The complete design and runbook are in [ALERT_PIPELINE.md](./ALERT_PIPELINE.md). The production flow is:

1. The scanner holds a distributed lease, updates the Blob snapshot/state, and durably writes deterministic `stock.available.v1` events to `alertoutbox` before advancing alert state.
2. `airco-alert-publisher-job` runs every minute and publishes pending rows to topic `stock-events`.
3. `airco-alert-fanout-coordinator` consumes subscription `email-fanout` and creates 32 shard jobs on `email-fanout-jobs`.
4. Up to 16 fan-out workers stream the matching `alertrecipients` partition and enqueue opaque recipient-ID jobs on `email-jobs`.
5. The email worker reloads the latest recipient record, rechecks entitlement/country/event age, claims an ETag-guarded `alertdeliveries` row, and sends through ACS with a deterministic operation ID.

Supporting schedules:

- `airco-alert-reconciler-job`: daily `03:17 UTC`, repairs the web-maintained projection from canonical `users`.
- `airco-alert-retention-job`: daily `02:17 UTC`, removes published outbox rows after 30 days and terminal delivery rows after 90 days.

Service Bus stock/fan-out messages have a one-day TTL; email jobs and application event freshness are six hours. Duplicate detection is seven days. Invalid/permanent messages are dead-lettered rather than silently completed.

## Cross-repository recipient contract

The web/auth change adds a stable UUID `userId`. Changing an email address preserves this ID. Registration, profile preference changes, Stripe subscription webhooks, cancellation, and account deletion synchronize `alertrecipients`.

The projection contract is fixed at 32 partitions (`r-00`…`r-1f`) using the low five bits of `sha256(userId)`. It contains only the current email, language, delivery country, plan/status/period end, enabled flag, and synchronization metadata needed for alerts. A shard-count change requires a coordinated versioned migration in both repositories.

The backend reconciler supports deterministic UUID backfill for legacy rows and uses optimistic/safe deletion rules. It is a daily repair path, not an event-time dependency on a full `users` scan.

## Security and privacy

- Production uses Entra ID/OAuth and user-assigned Managed Identity. Service Bus and ACS local authentication are disabled; Storage defaults to OAuth and the Blob container is private.
- Scanner/shared web runtime, publisher, fan-out, and email delivery use separate identities. New pipeline permissions are entity/table-scoped wherever Azure RBAC permits. GitHub deploys with OIDC and a custom least-privilege role; it cannot create role assignments or read application secrets.
- Queue messages never contain an email address, nickname, Stripe/customer/payment identifiers, or card data. `alertdeliveries` also stores no address.
- The email address exists only in canonical `users` and the minimal `alertrecipients` projection. The email worker resolves it immediately before sending and logs only a masked form.
- Production has no `EMAIL_TO`/`notification-email` fallback. Failure to read current entitlement/address must fail closed.
- Key Vault is reserved for actual third-party adapter credentials; secrets never enter Git, images, Bicep parameters, Service Bus payloads, or browser code.

## Scaling and current quota constraint

The scanner performs constant work with respect to subscriber count. Recipient expansion is independently scalable and page-streamed over 32 Table partitions. The canonical `users` table is read only by the daily reconciler, so manual user-table splitting is not needed for the hot path today.

Coordinator replicas scale to 4 and fan-out replicas to 16. Service Bus Standard entities use batching and deterministic duplicate detection. Monitor backlog age, active/dead-letter counts, throttling, pending-outbox age, delivery failures, and ACS `429` responses before changing topology.

The current Azure-managed ACS sender domain is the limiting component: approximately 5 messages/minute and 10/hour. The email app is therefore capped at one replica and spaces sends by 13 seconds. Before real user growth, verify `airco-tracker.eu` as a customer-managed ACS sender (SPF/DKIM), link it with foundation's `customEmailDomainId`, explicitly select it with `ACS_EMAIL_DOMAIN_NAME`, request a quota increase, then raise the email replica/rate settings. Raising worker count before quota is unsafe.

## Inventory and retailer semantics

- There are 45 active credential-free adapters: 28 Dutch and 17 French. README contains the authoritative active list and per-retailer notes.
- Track genuine compressor air conditioners. Exclude air coolers, fans, accessories, quote-only items, fixed split systems outside the supported portable scope, store-only/pickup-only products, expired deals, and multi-week lead times.
- Presale can appear in the dashboard but never triggers an immediate-stock email. Presale-to-immediate is a valid restock transition.
- One retailer failure cannot stop others. A failed site retains the last successful inventory with `status: error` / `stale: true`, and alert state is updated only for successful sites.
- Live inventory and alert state remain separate. Inventory schema version `1` is a production cross-repository contract; breaking changes require an explicit version bump and coordinated frontend/backend release.
- Direct 403/anti-bot candidates are documented in [RETAILER_403_BACKLOG.md](./RETAILER_403_BACKLOG.md). Do not bypass CAPTCHA, robots restrictions, login walls, or anti-bot controls.

## External API status

- Conrad storefront access is Cloudflare-blocked. Use only the official Price & Availability API after allowlist/approval; never restore anti-bot scraping.
- AliExpress affiliate access was approved, but Open Platform application/key/official signing status must be reconfirmed before implementation. Read only catalog/affiliate scopes; do not collect buyer, order, payment, or other personal data.

## Verification required for this release

Run from the backend root:

```bash
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
az bicep build --file infra/foundation.bicep --stdout >/dev/null
az bicep build --file infra/job.bicep --stdout >/dev/null
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

Run from `~/airco-tracking-web` because the recipient projection is a cross-repository change:

```bash
pnpm test
pnpm typecheck
pnpm build
```

For the real targeted test, follow the Managed-Identity one-time execution procedure in `ALERT_PIPELINE.md`: reconcile recipients, invoke `pipeline-test` on the deployed publisher job with authorized opaque `--recipient-id` values, and let the production workers consume it. Do not grant a personal principal temporary data-plane roles. Completion requires each targeted delivery to reach `sent`, inbox receipt to be confirmed, and active/dead-letter counts on the subscription and both queues to return to zero. Never record recipient addresses in this handoff.

## Deployment order

Foundation/RBAC changes must be run locally by an Owner or equivalent role-assignment-capable principal; the GitHub deployer is intentionally unable to create RBAC:

```bash
cd ~/airco-tracking
az login
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

For normal application releases, pushing `main` runs tests, builds an immutable SHA-tagged image, deploys the jobs/apps, and verifies reconciler → scanner → publisher. If fresh RBAC has not propagated, wait and rerun `scripts/deploy-application.sh` rather than broadening permissions.

## Next concrete steps

1. Complete full backend and frontend verification and compile/validate both Bicep entry points.
2. Deploy foundation/RBAC first, rerun OIDC bootstrap, then push/deploy both coordinated repositories.
3. Run the Managed-Identity targeted real-mail test for both authorized accounts; record non-PII evidence and queue/DLQ health.
4. Verify a customer-managed `airco-tracker.eu` ACS domain and request a production quota increase before onboarding users at scale.
5. Add Azure Monitor alert rules for nonzero DLQ, sustained queue age/backlog, stale pending outbox rows, Service Bus errors/throttling, delivery failure spikes, and ACS quota responses.

## Updating this handoff

Replace stale state instead of appending a diary. Record exact deployed commit/image, workflow and execution identifiers, verification counts, remaining blocker, frontend-contract compatibility, and next action. Keep the Chinese and English files synchronized and omit PII/secrets.
