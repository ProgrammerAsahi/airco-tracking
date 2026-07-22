# Airco Tracker — current handoff

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

Last updated: 2026-07-22 UTC

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
- Deployed backend image/commit: `1dd3017607ea0f2e6c72a223cd5aa0b6b5f5d24e`
- Compatible deployed frontend commit: `d097c75c9850946be024920677eba6761174ac48`
- Latest successful backend CI/deploy workflows: `29963989458` / `29963989419`; production frontend revision: `airco-tracking-web--0000066`
- Latest foundation deployment: `airco-foundation` (succeeded 2026-07-17)
- GitHub hardening: both repositories' `main` branches require the `validate` status check and block force-push and deletion. Both deploy workflows are gated on the `production` GitHub environment with a required reviewer.
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

The ACS higher-quota request is **Open**; its private case identifier is kept outside this public repository. The requested portal tier is `250` (1,000 messages/minute and 3,000/hour), while the application will initially self-limit to at most 100/minute and 10,000/day for up to 1,000 initial users. Keep the deployed one-worker/13-second sender limit until Azure approves the request, then warm the domain gradually for two to four weeks.

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
- Web, scanner, retention, publisher, fan-out, email delivery, and delivery-report processing use separate identities. Production binds `aircontrack-identity` to the web app, `aircontrack-scanner` to the scanner job, `aircontrack-retention` to backend retention, `aircontrack-web-retention` to web-auth cleanup, `aircontrack-alert-publisher` to the publisher, `aircontrack-alert-fanout` to the reconciler/coordinator/fan-out workers, `aircontrack-alert-email` to the email worker, and `aircontrack-alert-delivery-report` to delivery processing and its DLQ cleanup. Pipeline permissions are entity/table-scoped wherever Azure RBAC permits. Vault-wide secret access has been replaced with exact secret scopes: web gets unsubscribe/withdrawal/OTP-pepper only, scanner gets Awin/AliExpress credentials only, and email gets unsubscribe only. Event Grid alone has the storage-account-scoped Blob role required by Azure's managed-identity dead-letter validation; the delivery publisher has only table-level read access to `alertdeliveries`. GitHub deploys with OIDC and a custom least-privilege role; it cannot create role assignments or read application secrets. `infra/github-oidc.bicep` issues both ref-scoped and environment-scoped federated credentials (`github-airco-tracking-env-production`, `github-airco-tracking-web-env-production`) on `airco-github-deployer`, because jobs declaring `environment:` receive an environment-scoped token subject.
- The old storage-account-wide `Storage Table Data Contributor` assignment has been removed, and the shared identity's blob data-plane access is now narrowed to the `airco-tracker` container. A custom role `aircontrack-acs-email-sender` (three actions, per Microsoft Learn SMTP-authentication guidance) replaced `Communication and Email Service Owner` for both `aircontrack-identity` and `aircontrack-alert-email`. The legacy broad assignments were deleted manually after verification. Key Vault `AuditEvent` diagnostics flow to the operations Log Analytics workspace, and a monthly €50 cost budget notifies the operations action group at 80% and 100% of actual spend. Key Vault soft-delete retention remains 7 days: Azure fixes it at vault creation and rejects changes, as documented in the template comment.
- Normal application queue messages never contain an email address, nickname, Stripe/customer/payment identifiers, card data, or the private canonical source-row pointer. The dedicated provider-report queue is the narrowly retained exception because ACS delivery events necessarily contain the recipient; its one-day TTL, private dead letter, and cleanup policy bound that exposure. `alertdeliveries`, `alertdeliveryindex`, and suppression rows retain only opaque IDs/fingerprints and normalized status.
- Outside that bounded provider-report path, the email address exists only in canonical `users` and the minimal `alertrecipients` projection. The email worker resolves it immediately before sending and logs only a masked form.
- Production has no `EMAIL_TO`/`notification-email` fallback. Failure to read current entitlement/address must fail closed.
- Key Vault stores the small set of required application and adapter secrets, including the unsubscribe signing key. Secret values never enter Git, images, Bicep parameters, Service Bus payloads, logs, or browser code.
- The image installs Python dependencies from `requirements.lock` (uv-generated, hashed) with `pip install --require-hashes`, pins the base image by digest, requires urllib3 ≥2.7 (clearing five PYSEC advisories), and declares `requires-python >=3.12`; pip-audit is blocking in CI and deploy.

## Scaling and current quota constraint

The scanner performs constant work with respect to subscriber count. Recipient expansion is independently scalable and page-streamed over 32 Table partitions. The canonical `users` table is streamed only by the daily reconciler; the email worker uses one authoritative point read per delivery (UUID row, or the reconciled legacy source row). Only not-yet-backfilled legacy projections use a bounded compatibility query. Manual user-table splitting is therefore not needed for the hot path today.

Coordinator replicas scale to 4 and fan-out replicas to 16. Service Bus Standard entities use batching and deterministic duplicate detection. Monitor backlog age, active/dead-letter counts, throttling, pending-outbox age, delivery failures, and ACS `429` responses before changing topology.

Production uses the verified customer-managed ACS sender domain `airco-tracker.eu`; Domain, SPF, DKIM, and DKIM2 are verified, the domain is linked while `AzureManagedDomain` remains available for rollback, and both applications explicitly select it with `ACS_EMAIL_DOMAIN_NAME`. The documented default custom-domain limit is 30 messages/minute and 100/hour. Delivery-failure, bounce, suppression, unsubscribe, and complaint-observation controls are operational, and the tier-250 quota request is open. The email app remains capped at one replica with a 13-second send interval until Azure approves the request. Raising worker count before approval is unsafe.

## Inventory and retailer semantics

- There are 46 active credential-free adapters: 28 Dutch and 18 French; Costway France was removed from the active registry after a persistent HTTP 403 and is retained as a deferred adapter. README contains the authoritative active list and per-retailer notes.
- Track genuine compressor air conditioners. Exclude air coolers, fans, accessories, quote-only items, fixed split systems outside the supported portable scope, store-only/pickup-only products, expired deals, and multi-week lead times.
- Presale can appear in the dashboard but never triggers an immediate-stock email. Presale-to-immediate is a valid restock transition. Alert and inventory paths share one presale detector, `with_detected_presale` in `adapters/base.py`; Klimaatshop/EP.nl multi-week lead times classify as presale and no longer trigger immediate-stock email.
- EcoFlow France reads the official France Shopify catalogue and product data. Shopify variant availability remains authoritative, preorder copy only classifies an orderable variant as presale, and outbound product links currently go directly to the official EcoFlow France product page.
- E.Leclerc France uses the retailer's official storefront live API for discovery and per-scan stock truth, then sends users through an Awin deep link using advertiser `15135` and publisher `2981827`. Immediate stock and presale offers are strictly separated; presale never becomes an immediate-stock alert.
- Trotec France treats the official storefront Algolia index as the sole authority for live stock and presale. `sold_out` is parsed from a strict known boolean/string set; an orderable status with a missing or unknown veto signal fails closed. Approved Awin advertiser `62319` is used only through the Link Builder Batch API after first-party classification. API-generated links are cached for one day and must match the validated canonical Trotec URL plus advertiser `62319` and publisher `2981827`; any request/item/cache validation failure falls back to the canonical URL and cannot stale stock. `Product.url` remains the state/deduplication/event identity. The bearer token is loaded from Key Vault secret `awin-publisher-api-token` into `AWIN_PUBLISHER_API_TOKEN`; secret-in-URL Legacy feeds are unsupported. Until a real CMP exists, every returned link is forced to `cons=0`, which suppresses Awin cookies and click identifiers (and therefore cannot be relied on for commission attribution). Affiliate UI/email disclosures are required before a click.
- GAMMA and KARWEI normally parse their category tiles. Azure receives a Vercel 429 from that host, so the production fallback uses the storefront-published read-only catalogue with a strict multi-field online-stock contract. A robots-declared sitemap can only confirm a safely empty product catalogue; sitemap membership never proves stock. Schema/key/index or sitemap drift fails closed and retains stale inventory.
- Action France fails closed because its search-page cards cannot verify online orderability. Action NL vetoes expired deals (`deal verlopen`). Costway NL fails closed when quantity markers are missing and detects page-level markup drift. Hubo availability markers are scoped to the product section.
- Lidl, Hubo, FlinQ, and Airco-Webwinkel seasonal empty catalogues are legitimate empty results rather than stale inventory.
- One retailer failure cannot stop others. A failed site retains the last successful inventory with `status: error` / `stale: true`, and alert state is updated only for successful sites.
- Live inventory and alert state remain separate. Inventory schema version `1` is a production cross-repository contract; breaking changes require an explicit version bump and coordinated frontend/backend release.
- Direct 403/anti-bot candidates are documented in [RETAILER_403_BACKLOG.md](./RETAILER_403_BACKLOG.md). Do not bypass CAPTCHA, robots restrictions, login walls, or anti-bot controls.

## External API status

- Conrad storefront access is Cloudflare-blocked. Use only the official Price & Availability API after allowlist/approval; never restore anti-bot scraping.
- AliExpress Affiliate Open Platform search and SKU-detail clients are implemented as country-specific diagnostics. Every request carries the actual delivery country, but the approved SKU contract exposes no documented stock/orderability field, so these adapters deliberately remain outside the production registry and cannot create inventory or alerts. Only catalog/affiliate scopes are used; buyer, order, payment, and other personal data are out of scope.

## Deployed hardening release

The deployed release separates web, scanner, and retention identities; makes `alertoutboxpending` an authoritative full-payload enqueue journal with a complete continuation-page legacy migration and crash-safe archive repair; excludes stale retailer products from all live totals and scrubs their product payload after the 24-hour diagnostic window; routes all retailer and production partner-API traffic through one bounded, redirect-validated fetch boundary with endpoint-specific MIME/size limits and explicit read-only POST retries; validates canonical and affiliate URL hosts; compacts unavailable product state after 90 days and removes tombstones after 365 days; and removes the fixed 5,000-row retention ceiling. CI validates pushes to `main` from the hash-locked dependency set.

The Owner-only `scripts/migrate-runtime-identities.sh --apply` migration was completed after workload and smoke verification. A subsequent dry-run was a clean no-op: it found every exact replacement grant and no enumerated legacy grant to remove. GitHub deliberately remains unable to perform RBAC cleanup. Full contract, rollback, idempotency, and production evidence are in [HARDENING.md](./HARDENING.md).

## Verification completed for this release

Backend image/commit `1dd3017607ea0f2e6c72a223cd5aa0b6b5f5d24e` is deployed and production-verified. CI workflow `29963989458` passed and deploy workflow `29963989419` completed after the required `production` approval.

- Backend: 413/413 unit tests, `compileall`, shell syntax, all Bicep builds, locked-dependency audit, and `git diff --check` passed before release.
- The first production retention execution exposed a real Azure SDK contract missed by local mocks: `TableClient.query_entities` requires a positional `query_filter`. Commit `1dd3017` now passes an explicit empty filter and asserts that call in regression coverage. The immutable image was redeployed and a real production retention rerun succeeded.
- Frontend commit `d097c75c9850946be024920677eba6761174ac48` is production revision `airco-tracking-web--0000066`; `/health`, `/ready`, localized legal content, and anonymous inventory denial passed smoke checks.
- Reconciler, scanner, publisher, retention, delivery cleanup, and all four backend apps run under their exact identities. All three Service Bus queues and the stock-event subscription ended at zero active and zero dead-letter messages.
- The least-privilege migration removed the enumerated broad table, Blob, and vault-wide secret grants only after replacements were verified. Its post-deployment dry-run is idempotent and reports neither obsolete grants nor further changes.
- Real production ACS verification messages were delivered to independent Outlook and Gmail recipients from the custom domain. SPF, DKIM, and DMARC passed, and the support `Reply-To` was present. There were no active entitled alert recipients at verification time, so the full fan-out canary correctly produced no delivery job rather than bypassing entitlement checks.
- Production Table `i18n` was reseeded to 64 entries across the `email` and `web` scopes, adding `legal_privacy_link`, `legal_terms_link`, `legal_imprint_link`, and `legal_affiliate_link` and syncing `checking_subscription` to the Heatwave Pass wording. The temporary user-level table grant used for seeding was revoked afterwards.

## Deployment order

Foundation/RBAC changes must be run locally by an Owner or equivalent role-assignment-capable principal; the GitHub deployer is intentionally unable to create RBAC:

```bash
cd ~/airco-tracking
az login
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

For normal application releases, pushing `main` runs tests and builds an immutable SHA-tagged image. An existing environment first runs a reconciler canary with that candidate image; only then are jobs/apps updated and reconciler → scanner → publisher verified. A failed update or verification automatically reapplies the captured previous image. The compatible frontend deploy keeps its previous revision at 100% traffic until the candidate revision passes direct smoke/readiness checks, and automatically restores prior traffic on failure. These controls are production-verified. If fresh RBAC has not propagated, wait and rerun rather than broadening permissions.

## Next concrete steps

1. Continue monitoring the ACS quota request (still **Open**) through its privately recorded case identifier, and answer Azure with the existing consent, authentication, unsubscribe, final-delivery, suppression, monitoring, and warm-up evidence. Keep one worker and the 13-second interval until Azure approves, then raise concurrency conservatively under the 100/minute and 10,000/day launch caps.
2. Warm the custom domain over two to four weeks, then tighten DMARC from observation-only `p=none` to `quarantine` and finally `reject` while reviewing aggregate reports, provider complaints, bounce/suppression rates, and inbox placement; keep failures below 1%.
3. Repeat a full stock-event → fan-out → ACS → final-delivery canary when at least one explicitly consenting, actively entitled test recipient exists; do not create an entitlement solely to force the test.
4. Complete the legal pages' private operator/VAT/refund/governing-law facts and obtain legal review. Stripe must remain in test mode and fail closed until the business and legal activation prerequisites are complete.
5. Mid-term items: E.Leclerc product-URL identity migration, BTU enrichment cache, pagination coverage, PaaS public-endpoint tightening or documented risk acceptance, and a sanctioned GAMMA/KARWEI feed or written permission (any contract failure remains fail-closed).

## Updating this handoff

Replace stale state instead of appending a diary. Record exact deployed commit/image, workflow and execution identifiers, verification counts, remaining blocker, frontend-contract compatibility, and next action. Keep the Chinese and English files synchronized and omit PII/secrets.
