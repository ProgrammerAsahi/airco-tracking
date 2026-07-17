# Airco Tracker — current handoff

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

Last updated: 2026-07-15 CEST (Europe/Amsterdam)

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
- Reconciler job: `airco-alert-reconciler-job`, `17 3 * * *` UTC
- Production mail provider: Azure Communication Services Email
- Deployed backend image/commit: `efeb50220e7b1d4a6a607f84d65038af620b3feb`
- Compatible deployed frontend commit: `db98ce83f7f46517a75fa9977d4985dc25d5eee1`
- Latest successful backend workflow: `29372369189`; latest successful frontend workflow: `29367033016`
- Latest foundation deployment: `airco-foundation` (succeeded 2026-07-11)
- GitHub production pause variable: `DEPLOYMENT_PAUSED=false`
- First-seen production alert policy: `ALERT_ON_FIRST_SEEN=true`
- Documentation-only pushes are ignored by the deployment workflow.

## Production email delivery and reputation controls

The email-delivery hardening release is deployed and production-tested:

- The authoritative MX and both monitored forwarding aliases were read back and verified with external canaries. DMARC has exactly one observation-only `p=none` record; aggregate reporting is provider-managed. Forwarding destinations remain outside Git and support-ticket documentation.
- Authentication and stock-alert messages use a structured support `Reply-To` on the custom domain.
- Active pass holders can enable or pause alert email independently of their pass entitlement and realtime-inventory access. Visible unsubscribe and RFC 8058 one-click unsubscribe use a versioned HMAC capability whose signing key is read from Key Vault.
- ACS recipient-level final delivery flows through Event Grid → dedicated Service Bus queue → separate delivery-report worker. The ledger records normalized final states, hard bounces suppress only the affected address, and no-resend checks remain authoritative.
- Raw recipient data is confined to the provider-report path: one-day queue TTL, no expiry-to-DLQ, private Event Grid dead letters with seven-day lifecycle deletion, and a daily Service Bus delivery-report DLQ privacy-cleanup job.
- Event Grid dead-letter/dropped/repeated-failure alerts, privacy-safe final-outcome queries, and ACS operation diagnostics are active. The first manual cleanup execution and a real Action Group notification both succeeded.

ACS higher-quota case `06bfd9d3-65c22af0-6d841855-b8dc-4aea-8d93-d2364a875032` is **Open**. The requested portal tier is `250` (1,000 messages/minute and 3,000/hour), while the application will initially self-limit to at most 100/minute and 10,000/day for up to 1,000 initial users. Keep the deployed one-worker/13-second sender limit until Azure approves the request, then warm the domain gradually for two to four weeks.

## Asynchronous alert pipeline

The complete design is in [ALERT_PIPELINE.md](./ALERT_PIPELINE.md); consent, domain reputation, final-delivery, suppression, retention, and quota procedures are in [EMAIL_DELIVERY.md](./EMAIL_DELIVERY.md). The deployed production flow is:

1. The scanner holds a distributed lease, updates the Blob snapshot/state, and durably writes deterministic `stock.available.v1` events to `alertoutbox` before advancing alert state.
2. `airco-alert-publisher-job` runs every minute and publishes pending rows to topic `stock-events`.
3. `airco-alert-fanout-coordinator` consumes subscription `email-fanout` and creates 32 shard jobs on `email-fanout-jobs`.
4. Up to 16 fan-out workers stream the matching `alertrecipients` partition and enqueue opaque recipient-ID jobs on `email-jobs`.
5. The email worker reloads the latest recipient record, rechecks entitlement/country/event age, claims an ETag-guarded `alertdeliveries` row, and sends through ACS with a deterministic operation ID.
6. ACS final-delivery events flow through Event Grid system topic `aircontrack-acs-email-events` and `acs-email-delivery-events`; the delivery worker normalizes the result, updates `alertdeliveries`/`alertdeliveryindex`, and applies address-specific hard-bounce suppression.

Supporting schedules:

- `airco-alert-reconciler-job`: daily `03:17 UTC` (`17 3 * * *`), repairs the web-maintained projection from canonical `users`.
- `airco-alert-retention-job`: daily `02:17 UTC`, removes published outbox rows after 30 days and terminal delivery rows after 90 days.
- `airco-delivery-dlq-cleanup`: daily `02:43 UTC`, removes raw provider reports from the dedicated delivery-report DLQ.

Service Bus stock/fan-out messages have a one-day TTL; email jobs and application event freshness are six hours. Delivery-report messages have a one-day TTL, `maxDeliveryCount=16`, and do not copy expired payloads to the DLQ. Duplicate detection is seven days. Invalid/permanent application messages are dead-lettered rather than silently completed.

## Cross-repository recipient contract

The web/auth service assigns a stable UUID `userId`; changing an email address preserves it. Registration, profile preference changes, Stripe one-time pass payment/refund webhooks, pass revocation, and account deletion synchronize `alertrecipients`.

The projection contract is fixed at 32 partitions (`r-00`…`r-1f`) using the low five bits of `sha256(userId)`. It contains only the current email, language, delivery country, `entitlementTier`, `entitlementStatus`, `entitlementExpiresAt`, enabled flag, and synchronization metadata needed for alerts. `alerts` and `radar` both receive email while only `radar` grants realtime inventory in the web service. The backend still reads legacy recurring-subscription fields during migration, but new pass fields are authoritative. A shard-count change requires a coordinated versioned migration in both repositories.

The backend reconciler supports deterministic UUID backfill for legacy rows, records a private canonical source-row pointer for constant-time authoritative delivery reads, and uses optimistic/safe deletion rules. It is a daily repair path, not an event-time dependency on a full `users` scan. A legacy source row is trusted only when re-deriving its UUID matches the requested recipient UUID.

## Four-language delivery contract

- User language supports `zh`, `nl`, `en`, and `fr` from canonical Profile through `alertrecipients` and the email worker's authoritative pre-send reload.
- Stock-alert subject, introduction, HTML title, price, destination country, footer, and visible unsubscribe link are localized. English, Dutch, and French use correct singular/plural forms; French prices use French separators. The visible unsubscribe URL preserves the recipient language, while the RFC 8058 one-click API URL remains language-neutral.
- `airco_tracker/i18n_local.json` is the complete seed source for both `email` and `web` Table partitions. Every key has exactly four non-empty values. The `web` map is synchronized value-for-value with the frontend fixture; production seeding must upsert it before or during release, then new processes must load it because the backend loader is process-cached.
- Retailer and product names plus retailer-supplied delivery wording remain verbatim source evidence; they are not machine-translated.

## Security and privacy

- Production uses Entra ID/OAuth and user-assigned Managed Identity. Service Bus and ACS local authentication are disabled; Storage defaults to OAuth and the Blob container is private.
- Scanner/shared web runtime, publisher, fan-out, email delivery, and delivery-report processing use separate identities. Pipeline permissions are entity/table-scoped wherever Azure RBAC permits. Event Grid alone has the storage-account-scoped Blob role required by Azure's managed-identity dead-letter validation; the delivery publisher has only table-level read access to `alertdeliveries`. GitHub deploys with OIDC and a custom least-privilege role; it cannot create role assignments or read application secrets.
- The old storage-account-wide `Storage Table Data Contributor` assignment has been removed. The shared runtime retains only the required per-table contributor/reader assignments plus its Blob role; production OTP, profile/projection writes, logout, retention, and scanner execution all passed after removal.
- Normal application queue messages never contain an email address, nickname, Stripe/customer/payment identifiers, card data, or the private canonical source-row pointer. The dedicated provider-report queue is the narrowly retained exception because ACS delivery events necessarily contain the recipient; its one-day TTL, private dead letter, and cleanup policy bound that exposure. `alertdeliveries`, `alertdeliveryindex`, and suppression rows retain only opaque IDs/fingerprints and normalized status.
- Outside that bounded provider-report path, the email address exists only in canonical `users` and the minimal `alertrecipients` projection. The email worker resolves it immediately before sending and logs only a masked form.
- Production has no `EMAIL_TO`/`notification-email` fallback. Failure to read current entitlement/address must fail closed.
- Key Vault stores the small set of required application and adapter secrets, including the unsubscribe signing key. Secret values never enter Git, images, Bicep parameters, Service Bus payloads, logs, or browser code.

## Scaling and current quota constraint

The scanner performs constant work with respect to subscriber count. Recipient expansion is independently scalable and page-streamed over 32 Table partitions. The canonical `users` table is streamed only by the daily reconciler; the email worker uses one authoritative point read per delivery (UUID row, or the reconciled legacy source row). Only not-yet-backfilled legacy projections use a bounded compatibility query. Manual user-table splitting is therefore not needed for the hot path today.

Coordinator replicas scale to 4 and fan-out replicas to 16. Service Bus Standard entities use batching and deterministic duplicate detection. Monitor backlog age, active/dead-letter counts, throttling, pending-outbox age, delivery failures, and ACS `429` responses before changing topology.

Production uses the verified customer-managed ACS sender domain `airco-tracker.eu`; Domain, SPF, DKIM, and DKIM2 are verified, the domain is linked while `AzureManagedDomain` remains available for rollback, and both applications explicitly select it with `ACS_EMAIL_DOMAIN_NAME`. The documented default custom-domain limit is 30 messages/minute and 100/hour. Delivery-failure, bounce, suppression, unsubscribe, and complaint-observation controls are operational, and the tier-250 quota request is open. The email app remains capped at one replica with a 13-second send interval until Azure approves the request. Raising worker count before approval is unsafe.

## Inventory and retailer semantics

- There are 47 active credential-free adapters: 28 Dutch and 19 French. README contains the authoritative active list and per-retailer notes.
- Track genuine compressor air conditioners. Exclude air coolers, fans, accessories, quote-only items, fixed split systems outside the supported portable scope, store-only/pickup-only products, expired deals, and multi-week lead times.
- Presale can appear in the dashboard but never triggers an immediate-stock email. Presale-to-immediate is a valid restock transition.
- EcoFlow France reads the official France Shopify catalogue and product data. Shopify variant availability remains authoritative, preorder copy only classifies an orderable variant as presale, and outbound product links currently go directly to the official EcoFlow France product page.
- E.Leclerc France uses the retailer's official storefront live API for discovery and per-scan stock truth, then sends users through an Awin deep link using advertiser `15135` and publisher `2981827`. Immediate stock and presale offers are strictly separated; presale never becomes an immediate-stock alert.
- Trotec France treats the official storefront Algolia index as the sole authority for live stock and presale. `sold_out` is parsed from a strict known boolean/string set; an orderable status with a missing or unknown veto signal fails closed. Approved Awin advertiser `62319` is used only through the Link Builder Batch API after first-party classification. API-generated links are cached for one day and must match the validated canonical Trotec URL plus advertiser `62319` and publisher `2981827`; any request/item/cache validation failure falls back to the canonical URL and cannot stale stock. `Product.url` remains the state/deduplication/event identity. The bearer token is loaded from Key Vault secret `awin-publisher-api-token` into `AWIN_PUBLISHER_API_TOKEN`; secret-in-URL Legacy feeds are unsupported. Until a real CMP exists, every returned link is forced to `cons=0`, which suppresses Awin cookies and click identifiers (and therefore cannot be relied on for commission attribution). Affiliate UI/email disclosures are required before a click.
- GAMMA and KARWEI normally parse their category tiles. Azure receives a Vercel 429 from that host, so the production fallback uses the storefront-published read-only catalogue with a strict multi-field online-stock contract. A robots-declared sitemap can only confirm a safely empty product catalogue; sitemap membership never proves stock. Schema/key/index or sitemap drift fails closed and retains stale inventory.
- One retailer failure cannot stop others. A failed site retains the last successful inventory with `status: error` / `stale: true`, and alert state is updated only for successful sites.
- Live inventory and alert state remain separate. Inventory schema version `1` is a production cross-repository contract; breaking changes require an explicit version bump and coordinated frontend/backend release.
- Direct 403/anti-bot candidates are documented in [RETAILER_403_BACKLOG.md](./RETAILER_403_BACKLOG.md). Do not bypass CAPTCHA, robots restrictions, login walls, or anti-bot controls.

## External API status

- Conrad storefront access is Cloudflare-blocked. Use only the official Price & Availability API after allowlist/approval; never restore anti-bot scraping.
- AliExpress affiliate access was approved, but Open Platform application/key/official signing status must be reconfirmed before implementation. Read only catalog/affiliate scopes; do not collect buyer, order, payment, or other personal data.

## Verification completed for this release

Backend image/commit `efeb50220e7b1d4a6a607f84d65038af620b3feb` is deployed and production-verified. It passes 239/239 backend tests, translation completeness and frontend-map equality checks, JSON parsing, `compileall`, and `git diff --check`. Unit coverage includes French Profile projection, worker reload, France/Netherlands destination wording, French/Dutch price formats, singular/plural email HTML and text, French unsubscribe navigation, and strict EcoFlow/E.Leclerc immediate-stock-versus-presale handling.

- Backend: 239/239 unit tests, compileall, shell syntax, both Bicep entry points, `git diff --check`, and live retailer parsing passed.
- Frontend: 71/71 tests, app/server typecheck, production build, Bicep/deployment verification, and production HTTP checks passed. French Landing, Subscribe, Profile, login, and unsubscribe states passed desktop and narrow visual checks; the production browser console had no warning or error.
- GitHub workflow `29371474576` performed the silent paused deployment. Seed execution `airco-tracker-job-ig2bh32` succeeded and logged `No new stock` / `no outbox`, preventing first-seen mail from the two newly enabled retailers.
- Restore workflow `29372369189` succeeded. Its verification executions—reconciler suffix `vi4r23m`, scanner suffix `1cpvmx0`, and publisher suffix `czszdod`—all succeeded. The final production settings are `DEPLOYMENT_PAUSED=false` and `ALERT_ON_FIRST_SEEN=true`.
- GitHub continues to serve compatible frontend SHA `db98ce8…` from workflow `29367033016`; the frontend image is serving revision `airco-tracking-web--0000053` at 100% traffic.
- Production Table `i18n` was seeded with 56 entries across the `email` and `web` scopes. The 38-key web map exposes four non-empty languages and matches the frontend fallback value-for-value. The temporary table-scoped seed/support permissions were removed and read back as zero assignments.
- Event Grid system topic/subscription, the `email-fanout` subscription, all three queues, both delivery tables, the seven-day dead-letter lifecycle, seven metric alerts, and two scheduled-query alerts are enabled. The four inspected broker entities ended with zero active, scheduled, transfer-DLQ, and DLQ messages.
- The customer-managed ACS domain reports `Succeeded`; Domain/SPF/DKIM/DKIM2 are `Verified`, it is linked to the Communication Service, and `AzureManagedDomain` remains linked as fallback. Production sender identity is `Airco Tracker <DoNotReply@airco-tracker.eu>`.
- Targeted production event `a4ec09309cd8fa12ba09881f27ea635d5a05baa7420654495ffce4fc024b5ead` reached final `delivered` state for both authorized recipients, and both monitored providers placed it in the inbox. Gmail original headers showed aligned SPF, DKIM, and DMARC passes, the expected Reply-To, an HTTPS `List-Unsubscribe`, and exact RFC 8058 `List-Unsubscribe-Post` semantics. No recipient address is recorded here.
- A production French OTP reached the authorized Outlook inbox from the custom-domain sender with French subject, title, expiry, and safety copy. French canary event `a78f237c1ae49be79519c4049c11f4876864ae224b5b77f630cfe9cbb3ed33df` then reached final `delivered` for the authorized Gmail test account; subject, body, and visible pause-alert link were French. The Profile preference was restored after the test, and the topic subscription plus all three queues returned to zero active and dead-letter messages.
- A real one-click POST paused alert email without login while leaving the paid subscription and inventory entitlement unchanged. Re-enabling alerts rotated the capability; the old link remained idempotent but could not change the new state.
- External inbound-forwarding canaries reached both monitored mailboxes. One initial support-forwarding canary landed in spam, so gradual warm-up and reputation monitoring remain required. DMARC stays at observation-only `p=none` while aggregate reports and legitimate senders are reviewed.
- Production verification returned 47 sites and 92 available products, with one stale site. EcoFlow France returned 16 products / 2 available; E.Leclerc France returned 18 products / 16 available. Costway France returned HTTP 403 and correctly retained its previous result as stale instead of inventing or clearing stock.
- On 17 July 2026, Costway France was removed from the active production registry and retained as a deferred adapter because the HTTP 403 persisted. The next scanner snapshot removes the stale site automatically; Costway NL remains active and unaffected.
- The first restored natural cron, `airco-tracker-job-29734470`, also succeeded. EcoFlow and E.Leclerc remained at 16/2 and 18/16; an unrelated genuine De'Longhi PACEM90K.1SILENT immediate-stock transition raised the snapshot to 93 available products. The publisher emitted exactly that one event, then the outbox returned to empty, confirming the two new adapters did not create first-seen noise.
- The publisher outbox was empty after restoration and verification. GAMMA/KARWEI continue to use the strict public-catalogue fallback when their category host rate-limits Azure; schema/key/index drift still fails closed rather than inventing stock.
- `/`, `/health`, and `www` health returned 200; anonymous `/api/inventory` returned 401 as required. The monitoring Action Group sent a real inbox notification, and the first delivery-DLQ cleanup execution succeeded.

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

1. Monitor quota case `06bfd9d3-65c22af0-6d841855-b8dc-4aea-8d93-d2364a875032` and answer Azure with the existing consent, authentication, unsubscribe, final-delivery, suppression, monitoring, and warm-up evidence. Do not describe the request as approved until Azure confirms it.
2. Keep one worker and the 13-second interval until approval. After approval, raise concurrency conservatively, retain the application-level 100/minute and 10,000/day launch caps, and validate backlog, ACS `429`, final outcomes, and suppression under load.
3. Warm the custom domain over two to four weeks while reviewing DMARC aggregates, provider complaints, bounce/suppression rates, adverse final outcomes, and inbox placement; keep failures below 1%.
4. Continue monitoring GAMMA/KARWEI public catalogue key/index/schema health and seek a sanctioned feed or written permission for long-term use; any contract failure remains fail-closed rather than generating false stock.

## Updating this handoff

Replace stale state instead of appending a diary. Record exact deployed commit/image, workflow and execution identifiers, verification counts, remaining blocker, frontend-contract compatibility, and next action. Keep the Chinese and English files synchronized and omit PII/secrets.
