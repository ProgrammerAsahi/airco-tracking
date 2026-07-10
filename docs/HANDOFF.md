# Airco Tracker — current handoff

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

Last updated: 2026-07-10 (Europe/Amsterdam)

Update this English file and `HANDOFF.zh.md` together. Do not record secrets, email addresses, access tokens, payment data, or unnecessary personal information.

## Current objective

Operate a reliable portable-air-conditioner tracker for delivery to Dutch and French addresses, with a country-oriented design ready for more European markets. The scanner runs every ten minutes, keeps the private inventory snapshot current, and produces a stock event only for a first-seen or newly restocked immediate product that passes alert filters.

The released architecture replaces synchronous per-user sending with an Azure Service Bus Standard pipeline. Subscriber growth does not increase retailer-scan latency, and mail-provider failures do not prevent inventory/state progress.

## Repository and production

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking`
- Branch/local path: `main`, `~/airco-tracking`
- Resource group: `airco-tracker-rg`
- Frontend/auth repository: `https://github.com/ProgrammerAsahi/airco-tracking-web`
- Public site: `https://airco-tracker.eu/`
- Private inventory contract: Blob `airco-tracker/inventory.json`, schema version `1`
- Scanner job: `airco-tracker-job`, `*/10 * * * *` UTC
- Publisher job: `airco-alert-publisher-job`, `* * * * *` UTC
- Production mail provider: Azure Communication Services Email
- Deployed backend image/commit: `bfe6b407be84831cf961149cc617956945174ab0` (core pipeline commit `cd8acbb2aa9544b2d6c79d072c9a3373323da9f3`)
- Compatible frontend commit: `715acf223377d6b450a2a594e32eee0515a85797`
- Successful backend workflow runs: `29060991005` and `29063024406`; successful frontend run: `29061171454`
- Foundation migration deployment: `airco-foundation-partition-migration-20260710`
- GitHub production pause variable: `DEPLOYMENT_PAUSED=false`
- Documentation-only pushes are ignored by the deployment workflow.

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

The backend reconciler supports deterministic UUID backfill for legacy rows, records a private canonical source-row pointer for constant-time authoritative delivery reads, and uses optimistic/safe deletion rules. It is a daily repair path, not an event-time dependency on a full `users` scan. A legacy source row is trusted only when re-deriving its UUID matches the requested recipient UUID.

## Security and privacy

- Production uses Entra ID/OAuth and user-assigned Managed Identity. Service Bus and ACS local authentication are disabled; Storage defaults to OAuth and the Blob container is private.
- Scanner/shared web runtime, publisher, fan-out, and email delivery use separate identities. New pipeline permissions are entity/table-scoped wherever Azure RBAC permits. GitHub deploys with OIDC and a custom least-privilege role; it cannot create role assignments or read application secrets.
- The old storage-account-wide `Storage Table Data Contributor` assignment has been removed. The shared runtime retains only the required per-table contributor/reader assignments plus its Blob role; production OTP, profile/projection writes, logout, retention, and scanner execution all passed after removal.
- Queue messages never contain an email address, nickname, Stripe/customer/payment identifiers, card data, or the private canonical source-row pointer. `alertdeliveries` also stores no address.
- The email address exists only in canonical `users` and the minimal `alertrecipients` projection. The email worker resolves it immediately before sending and logs only a masked form.
- Production has no `EMAIL_TO`/`notification-email` fallback. Failure to read current entitlement/address must fail closed.
- Key Vault is reserved for actual third-party adapter credentials; secrets never enter Git, images, Bicep parameters, Service Bus payloads, or browser code.

## Scaling and current quota constraint

The scanner performs constant work with respect to subscriber count. Recipient expansion is independently scalable and page-streamed over 32 Table partitions. The canonical `users` table is streamed only by the daily reconciler; the email worker uses one authoritative point read per delivery (UUID row, or the reconciled legacy source row). Only not-yet-backfilled legacy projections use a bounded compatibility query. Manual user-table splitting is therefore not needed for the hot path today.

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

## Verification completed for this release

- Backend: 169/169 unit tests, compileall, shell syntax, both Bicep entry points, and `git diff --check` passed.
- Frontend: 59/59 tests, typecheck, production build, Bicep/deployment verification, and production HTTP checks passed.
- GitHub deployed immutable SHAs successfully. The Service Bus topic and both queues are `Active`, partitioned, and use seven-day duplicate detection; the subscription uses a five-minute lock and `maxDeliveryCount=8`.
- Targeted production event `f13967a78d7da2d2c1590d419fffbe969cdd4864175b2a8132cef8afc8a133c6` ran as `airco-alert-publisher-job-8e1cnu7`. Deliveries `3595247c55c2…` and `21514ad2068d…` reached `sent`, ACS accepted both, and both authorized inboxes received them. No recipient address is recorded here.
- A preceding fail-closed preflight exposed legacy rows without canonical UUID source pointers. It sent no mail; commit `bfe6b40` added strict legacy source-row resolution before the successful targeted run.
- After removing the broad Storage Table role, a real OTP login, language write/restore, projection sync, and logout all returned 200. Retention execution `airco-alert-retention-job-6u70ukl` and scanner execution `airco-tracker-job-ncdtvul` succeeded; the scanner saved 75 available products across 45 sites and persisted four real outbox transitions.
- Restoring normal schedules drove those transitions through the pipeline (fan-out backlog peaked at 128 and email backlog at 2). ACS accepted deliveries `3548668d33cb…` and `00eda20addd6…`; final active, scheduled, transfer-DLQ, and DLQ counts were zero on the subscription and both queues.
- Final custom-domain checks: `/`, `/health`, and `www` health returned 200; anonymous `/api/inventory` returned 401 as required.

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

1. Verify a customer-managed `airco-tracker.eu` ACS domain and request a production quota increase before onboarding users at scale.
2. Keep the four deployed Service Bus namespace alerts enabled; add application-level alerts for stale pending outbox rows, delivery-failure spikes, ACS `429` responses, and a scheduled end-to-end inbox canary.
3. Monitor the latest scanner warning for GAMMA and KARWEI parser drift while their last successful inventory remains safely marked stale.

## Updating this handoff

Replace stale state instead of appending a diary. Record exact deployed commit/image, workflow and execution identifiers, verification counts, remaining blocker, frontend-contract compatibility, and next action. Keep the Chinese and English files synchronized and omit PII/secrets.
