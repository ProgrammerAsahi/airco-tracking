# Airco Tracker — runtime hardening and migration

<p align="center">
  <a href="./HARDENING.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HARDENING-简体中文-d73a49"></a>
  <a href="./HARDENING.md"><img alt="English" src="https://img.shields.io/badge/HARDENING-English-0969da"></a>
</p>

This runbook describes the additive inventory contract, outbound-URL boundary, bounded fetcher, state/alert retention, pending-outbox index, and the Owner-only runtime-identity migration introduced by the July 2026 hardening release. The release and migration are production-verified; the procedure remains here as the authoritative rollback and future-environment runbook.

## Inventory freshness contract

Inventory schema version remains `1`; all new fields are additive. Root totals include only sites whose latest scan succeeded. Each site now exposes `freshness` (`verified` or `stale`), `counts_toward_totals`, `stale_age_seconds`, and `stale_too_old`. Root fields add `verified_site_count`, `inventory_confidence` (`verified`, `partial`, or `unavailable`), and `stale_diagnostic_max_age_seconds` (86,400 seconds).

A failed retailer retains its last successful products only as short-lived diagnostic evidence. It must never increase live-stock, immediate-stock, or presale totals. Once `stale_too_old=true` (after 24 hours, or when no trustworthy success timestamp exists), the producer scrubs the product list while retaining retailer health and timestamps. Consumers must never offer stale rows as purchasable inventory.

## URL and fetch boundaries

Every persisted canonical product URL must be HTTPS and match the explicit merchant host set for its country/site. Affiliate links are limited to approved Awin/AliExpress redirect hosts. User-info, control characters, fragments, oversized URLs, non-443 ports, unknown real merchants, and mismatched event URLs fail closed. When adding or renaming an adapter, update `MERCHANT_HOSTS_BY_SITE_ID` and its coverage test in the same change.

The shared fetcher disables automatic redirects and follows at most three redirects. Only the exact origin hostname, its strict `www`/bare peer, or a per-call explicitly permitted host is accepted; sibling subdomains are never inferred from public-suffix labels. Every call accepts only its MIME allow-list and streams the body into its own maximum. Ordinary HTML retains the 10 KiB anti-bot-shell minimum. Compact, valid JSON/XML endpoints explicitly opt into a smaller minimum, so a legitimate `{"products": []}` or short sitemap is accepted without weakening HTML checks. Sensitive headers are never forwarded to another host, even when that host is otherwise an allowed redirect target.

Every retailer, sitemap, Algolia/Nosto/Shopify endpoint, E.Leclerc API call, Awin production link-builder call, and production AliExpress call goes through this boundary. `POST` is single-attempt by default. Only explicitly documented, logically read-only catalogue queries opt into two bounded retries; mutations must never do so. `tests/test_adapter_transport_boundary.py` statically rejects adapter imports of HTTP clients, direct `.session` access, and direct `.post` calls, while the focused fetcher tests cover redirect, MIME, size and retry behavior.

## Bounded state and retention

Unavailable product state keeps full diagnostics for 90 days, then becomes a minimal tombstone, and is removed after 365 days. The availability generation survives compaction, so a short-term reappearance still produces the correct restock transition. These periods are configured by `STATE_COMPACT_AFTER_DAYS` and `STATE_TOMBSTONE_RETENTION_DAYS`.

The daily retention job has no fixed row cap by default and drains Azure Table continuation pages. Its production runtime budget is 240 seconds; an explicit cap or exhausted runtime emits a warning and leaves the remaining backlog for the next run. For a deliberately capped investigation use `cleanup-alert-data --limit N`; normal cleanup uses `--limit 0`.

## Pending-outbox index and concurrency

`alertoutboxpending` is the hot partition used by the minutely publisher and is now the authoritative enqueue journal, not a pointer-only index. One deterministic row contains the complete immutable event payload; that single Azure Table insert is the durable enqueue commit point. The scanner then writes a sharded `alertoutbox` archive row, but an archive timeout cannot hide or lose the event because the publisher reads the journal directly and repairs the archive before acknowledging the journal. This avoids pretending that Azure Table can provide a transaction across two partitions.

Publishers acquire each journal row through an ETag-guarded two-minute lease. Overlapping executions cannot hold the same live claim; a process crash leaves the complete event in place and the lease expires for recovery. A failed publish releases its own current lease without clearing a newer owner's lease.

The publisher never deletes a full journal row merely because the archive is temporarily absent. After Service Bus accepts an event, it first creates or conditionally marks the archive `published`, and only then idempotently deletes the journal. A crash at either boundary causes a safe retry with the same deterministic Service Bus `MessageId`. Pointer-only rows from the previous protocol are retained when their archive is absent, so a rolling-deployment writer cannot be stranded; rows with an existing pending archive are converted to full journals by the legacy migration. That migration drains the entire Azure iterator—including every continuation page—before writing the versioned `_meta/journal-v2` marker; regression coverage uses 1,205 legacy rows. Normal discovery queries only the single `pending` partition and never scans the sharded archive table on the publisher hot path.

Overlapping publisher executions are safe: deterministic event IDs and Service Bus `MessageId` values combine with recoverable ETag leases, seven-day duplicate detection, ETag-guarded archive transitions, and idempotent journal acknowledgement. The system is at-least-once; duplicate suppression and ledgers make repeats observable and harmless, not mathematically impossible.

## Runtime identities

- `${prefix}-identity`: web only; Blob reader on `airco-tracker`, contributor on `users`, `authcodes`, `authsessions`, and `alertrecipients`, i18n reader, the custom ACS sender role, and secret-level `Key Vault Secrets User` only on `unsubscribe-signing-key`, `withdrawal-signing-key`, and `auth-code-hmac-pepper`.
- `${prefix}-scanner`: scanner only; Blob contributor, contributor on `alertoutbox` and `alertoutboxpending`, ACR pull, and secret-level reads only for `awin-publisher-api-token`, `aliexpress-app-key`, and `aliexpress-app-secret`.
- `${prefix}-retention`: backend retention only; contributor on alert outbox/pending/delivery/index/suppression tables, reader on `alertrecipients`, and ACR pull.
- `${prefix}-web-retention`: web-auth retention only; contributor on `users`/`authcodes`/`authsessions` and ACR pull, with no alert-pipeline, ACS, or Key Vault access.
- `${prefix}-alert-publisher`: publisher only; reads and updates pending/archive outbox rows and sends only to the `stock-events` topic.
- `${prefix}-alert-fanout`: reconciler/coordinator/fan-out only; reads the minimum recipient projection, consumes `email-fanout`, and sends opaque recipient jobs to the two fan-out queues.
- `${prefix}-alert-email`: email worker only; its existing queue/table/ACS permissions plus a secret-level read only for `unsubscribe-signing-key`.
- `${prefix}-alert-delivery-report`: ACS delivery-report worker and DLQ cleanup only; consumes the delivery-event queue and updates delivery/index/suppression state without stock-publish or email-send permission.

No runtime has vault-wide secret-list/read access after migration. Secret values remain out of band. `manageSecretScopedKeyVaultRbac` is deliberately disabled in a bare Bicep invocation until all six named secrets exist; `deploy-azure.sh` checks secret metadata through the ARM control plane and enables the assignments automatically only when the set is complete. It never reads a secret value.

Foundation outputs `webIdentity*`, `scannerIdentity*`, `retentionIdentity*`, and `webRetentionIdentity*`; legacy `identityName` remains an alias for the web identity so the frontend deployment contract is not broken.

## Safe migration and rollback

Foundation/RBAC deployment must be run by an Owner-equivalent local principal. GitHub intentionally has no role-assignment permission.

1. Provision all six named Key Vault secrets. Run `AZURE_FOUNDATION_ONLY=true ./scripts/deploy-azure.sh`; for an existing complete vault it creates the exact secret-scoped assignments and all replacement identities/RBAC without moving any workload. To fail closed instead of auto-detecting, also set `AZURE_MANAGE_SECRET_SCOPED_KEY_VAULT_RBAC=true`.
2. Deploy the web repository so its cleanup Job binds `${prefix}-web-retention`, then run `./scripts/deploy-application.sh` so scanner and backend retention bind their dedicated identities. Application rollback restores both the previous image and the scanner/retention identity names captured before deployment.
3. Verify scanner, backend retention, web-auth retention, publisher, web login/profile, inventory API, and one authorized targeted email test. Confirm the web app, scanner job, backend retention job, web cleanup job, and email worker use their exact identities.
4. Run `AZURE_RESOURCE_GROUP=... AZURE_PREFIX=... ./scripts/migrate-runtime-identities.sh`. It changes nothing and prints only the exact obsolete grants.
5. Only after the checks pass, run the same command with `--apply`. It first verifies every replacement workload binding, every exact secret-level grant, and every retained web/email/scanner grant; it then removes only the enumerated legacy grants, including the three vault-wide `Key Vault Secrets User` assignments and any backend-retention grants on auth tables, and verifies both retained and removed states again.
6. Rerunning dry-run or `--apply` is safe: absent grants are a no-op and concurrent cleanup is tolerated.

Rollback before cleanup by restoring the old job identity and its old grants. After cleanup, reassign only the exact required role that failed and redeploy; do not grant subscription/resource-group Contributor. Bicep incremental deployment does not remove the old role assignments automatically, so the explicit apply step is the only destructive boundary.

## Application canary and rollback

`deploy-application.sh` builds an immutable candidate and, when an earlier deployment exists, first runs the recipient reconciler once from a mode-`0600`, schema-filtered execution template. The template copies the deployed command, arguments, environment, and CPU/memory while replacing only the image; ambiguous multi-container or volume-backed templates fail closed. `job start --yaml` uses the deployed identity and dependencies without changing any production job definition. Only a successful canary proceeds to the Bicep update. The normal reconciler → scanner → publisher checks then run against the deployed image. An `EXIT` guard automatically redeploys every application workload with the captured previous image if the update or a post-deployment check fails, and re-verifies the reconciler.

This automatic rollback restores executable code, not an arbitrary older infrastructure schema: it intentionally reapplies the current reviewed Bicep parameters with the previous immutable image. A release that intentionally changes incompatible resource configuration must retain the previous Git commit and use its template for a full configuration rollback. The web repository independently keeps the last healthy Container Apps revision at 100% traffic, verifies a zero-traffic candidate through its revision FQDN (including `/ready` dependency access), and shifts traffic only after success; any failure restores 100% traffic to the prior revision.

## Production verification

- Backend image/commit `1dd3017607ea0f2e6c72a223cd5aa0b6b5f5d24e` passed 413/413 unit tests and CI workflow `29963989458`; deploy workflow `29963989419` completed through the required production approval. The compatible frontend is commit `d097c75c9850946be024920677eba6761174ac48`, revision `airco-tracking-web--0000066`.
- The Owner applied `migrate-runtime-identities.sh --apply` only after workload bindings and replacement grants were verified. A post-deployment dry-run is a clean no-op: every exact replacement remains present, no enumerated legacy broad grant remains, and Bicep redeployment did not recreate one.
- Scanner, backend retention, web-auth cleanup, publisher, reconciler/coordinator/fan-out, email, and delivery-report workloads use the exact identities listed above. Health/readiness, localized legal content, anonymous inventory denial, Service Bus queue/subscription health, and production job executions passed.
- The first production retention run revealed a real SDK contract that local mocks had hidden: `TableClient.query_entities` requires a positional `query_filter`. Commit `1dd3017` passes an explicit empty filter and adds a regression assertion. After redeployment, a real production retention execution succeeded.
- Real ACS verification mail reached independent Outlook and Gmail providers. The custom-domain sender, support `Reply-To`, SPF, DKIM, and DMARC all verified. No active entitled alert recipient existed during the release window, so the full fan-out canary safely produced no delivery job; entitlement checks were not bypassed.
- Remaining external gates are the open ACS quota increase and sender warm-up, completion and professional review of private legal/business facts, and Stripe live activation. Stripe stays in test mode and fails closed until those prerequisites are complete.

## Verification gate

Before commit or deployment run:

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps .
python -m pip check
python -m unittest discover -v
python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
for file in infra/*.bicep; do az bicep build --file "$file" --stdout >/dev/null; done
git diff --check
```

CI runs on pull requests and pushes to `main`, uses the hash-locked dependency set, installs the project with `--no-deps`, and audits the same lock file. `.venv` (including a symlink with that name) is ignored and must never be committed.
