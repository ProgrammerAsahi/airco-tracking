# Airco Tracker — current handoff

Last updated: 2026-07-06 (Europe/Amsterdam)

## Current objective

Run a reliable, low-maintenance portable-air-conditioner stock tracker for delivery to Dutch and French addresses, with a country-based architecture ready for more European expansion. Production runs every ten minutes in Azure, maintains a complete current available-stock snapshot for the public dashboard, and sends an email only for first-seen or newly-restocked products that pass the alert filters.

The 2026-07-05 rename round generalized the project from `airco-tracking-nl` to `airco-tracking`: the GitHub repository was renamed, the 27 Dutch retailer adapters moved into `airco_tracker/adapters/nl/`, and a `registry.py` with `load_adapter_classes(countries)` replaced the hardcoded 28-adapter import list in `cli.py`. A `COUNTRIES` env var (default `nl`) selects active countries. The distribution package was renamed `airco-tracker-nl` → `airco-tracker`, the OIDC federated-credential subject was updated to the new repo path, and a pre-existing `i18n_local.json` packaging bug was fixed so a Table Storage outage no longer crashes the job. Azure resources were consolidated into a single new `airco-tracker-rg` resource group: 9 resources were moved from the old `airco-tracker-nl-rg`, and the 2 UAMIs + Communication EmailService (which do not support move) were recreated from scratch in the new RG with new clientIds/principalId and a new sender domain. The old `airco-tracker-nl-rg` is fully deleted. All 6 role assignments and 2 OIDC federated credentials were recreated on the new identities/resources.

The 2026-07-06 hardening round finished the migration pieces that were only adapter-level before: inventory and product records now include `country` and stable `site_id`, alert state is keyed by `country:url` with legacy URL fallback, and inventory snapshots expose separate `immediate_product_count` and `presale_product_count` while keeping `available_product_count` as the total visible orderable products. Site inventory records now also include `delivery_coverage`, a conservative list of destination tokens (ISO-2 countries plus `eu`/`eea`/`nordics`/`benelux`/`dach`) consumed by the frontend's `/deliver-to/<country>` routes. Presale products still appear on the dashboard but do not alert; a presale-to-immediate transition now triggers an alert. Azure clients now share a helper that explicitly uses the configured UAMI (`AZURE_CLIENT_ID`), and the Table i18n loader derives the Table endpoint from the Blob endpoint. `infra/job.bicep` now exposes a `countries` parameter and sets `COUNTRIES` in the Job environment.

The latest retailer round added the first stable France MVP batch: Castorama, Auchan, Rue du Commerce, Create France, Evolarshop France, Klarstein France, Trotec France, De'Longhi France, Lidl France, and Action France. France adapters split immediate stock from presale (`Pré-commande`, `Expédition à partir`, `livraison prévue semaine`, and multi-week lead times) and aggressively filter coolers/fans/accessories. Boulanger adapter code is present but deferred because the page is reachable locally while Azure Container Apps outbound requests consistently hit a 60-second read timeout; re-enable only after a stable page API or official/public alternative source is found. Cdiscount and E.Leclerc are deferred because direct responses are anti-bot/SPA shells without stable product data, and Leroy Merlin, Darty, ManoMano, Fnac, and Carrefour are direct-403 for normal requests. Prior rounds added Bostools as Dutch retailer 28, presale separation, the inventory snapshot, 27 retailers, Azure Key Vault recipient storage, EUR 1,500 / 7,000 BTU alert filters, bol.com removal, and a BTU accuracy audit. Conrad remains pending because its public pages reject automated requests and its official API requires separate approval.

## Repository and production

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking`
- Branch: `main`
- Feature commit: France expansion deployed
- Last verified production image: latest successful backend deployment from `main`; verify the exact immutable SHA tag with `az containerapp job show` when handing off.
- GitHub workflow: `Deploy to Azure`
- Azure resource group: `airco-tracker-rg` (all 12 resources consolidated here; old `airco-tracker-nl-rg` deleted)
- Deployer UAMI clientId (GitHub Actions `AZURE_CLIENT_ID`): `8adc0579-710f-4fcb-8762-28cea100a8a9`
- OIDC federated credential subject: `repo:ProgrammerAsahi/airco-tracking:ref:refs/heads/main` (updated from `airco-tracking-nl`)
- Container Apps job: `airco-tracker-job`
- Schedule: `*/10 * * * *` (UTC)
- Alert state: Azure Blob Storage, `airco-tracker/state.json`
- Live inventory: Azure Blob Storage, `airco-tracker/inventory.json`
- Notifications: Azure Communication Services Email
- Runtime identity: user-assigned Managed Identity (`aircontrack-identity`, clientId `ee7911d7-5ab9-4332-b9cc-b97fcd85d5d8`)
- Dashboard consumer repository: `https://github.com/ProgrammerAsahi/airco-tracking-web`
- Dashboard live URL: `https://airco-tracking-web.livelystone-5966d837.westeurope.azurecontainerapps.io/deliver-to/nl`
- Dashboard deployed image commit: `3ab350822b83185fe48f008447ab67d982ef5565`
- Dashboard handoff/docs head: see `airco-tracking-web` `main`; doc-only commits may use `[skip ci]` and do not imply a new deployed image.

## Active retailers

The application currently registers 38 credential-free adapters: 28 Dutch adapters and 10 French adapters.

Dutch adapters:

- Coolblue
- MediaMarkt NL
- EP.nl
- Electro World
- Wehkamp
- Lidl Netherlands
- GAMMA
- KARWEI
- Praxis
- Alternate.nl
- Trotec
- Klarstein
- FlinQ
- Action Webshop
- Expert.nl
- De'Longhi Netherlands
- Obelink
- Kampeerwereld
- Create Netherlands
- Costway NL
- Evolarshop
- Airco voor in huis
- Solago
- Hubo
- Vrijbuiter
- Klimaatshop
- Airco-Webwinkel
- Bostools

French adapters:

- Castorama
- Auchan
- Rue du Commerce
- Create France
- Evolarshop France
- Klarstein France
- Trotec France
- De'Longhi France
- Lidl France
- Action France

Deferred French adapters:

- Boulanger: local and GitHub-hosted requests can read the server-rendered search page, but Azure Container Apps outbound requests consistently hit a 60-second read timeout. The parser remains in `adapters/fr/boulanger.py`; keep it out of `ADAPTERS` until a stable page API or official/public alternative source is found.

Removed:

- bol.com: the official Marketing Catalog API terms reject stock-notification use cases, so the adapter, configuration (`BOL_*`), README references, and deploy-script wiring were deleted on 2026-07-02. Do not restore webpage scraping (Azure IPs get 403 and the search route is robots-restricted) and do not use search-engine snippets as a stock oracle — search indexes lag restocks and may mislabel `preorder`/`backorder` as in stock.

New-retailer stock semantics:

- Expert: only explicit online saleability counts; local-store-only stock is unavailable.
- De'Longhi: product JSON-LD is authoritative, but `Breng mij op de hoogte` forces unavailable.
- Obelink and Kampeerwereld: known seasonal URLs remain checked after products disappear from category pages.
- Kampeerwereld: `Exclusief in winkel` never counts as deliverable.
- Create: `Presale` and future `Verzending vanaf` dates are unavailable.
- Costway NL: Magento `qty-N` photo class drives stock; `qty-0` plus `UITVERKOCHT` label is unavailable. Split units are excluded.
- Evolarshop: category page is client-rendered via Nosto; the adapter queries the same public GraphQL search endpoint. "Zonder afvoerslang" (no exhaust hose) products are excluded as non-compressor units.
- Airco voor in huis: WooCommerce `instock`/`outofstock` class drives availability; only the mobiele-airco-systemen subcategory is tracked.
- Solago: Shopify product JSON-LD; `Voorbestelling` / `Levering vanaf` pre-order text overrides InStock schema. PortaSplit (portable split) is accepted; fixed split is excluded.
- Hubo: Shopify product sitemap discovery + JSON-LD detail pages; no category page exists, so sitemaps are scanned for portable-airco URLs.
- Vrijbuiter: category page links + @graph JSON-LD detail pages; portable split units for caravan/camper (Mestic SPA, Qlima MS-AC) are tracked.
- Klimaatshop: custom category grid; product URLs from `data-url` attribute, stock from `.stock` span.
- Airco-Webwinkel: WooCommerce store discovered via product sitemap; JSON-LD detail pages.
- Bostools: two server-rendered WooCommerce categories; dated availability is presale, short business-day lead time is immediate stock, and VAT-inclusive consumer price is authoritative.
- France MVP: Auchan/Rue du Commerce parse server-rendered cards or microdata; Castorama is tracked but current products are store-availability-only and not immediate online stock; Create France and Evolarshop France expose preorders as presale; Klarstein France and De'Longhi France are currently out of stock; Trotec France uses the public Algolia product index but requires name-level filtering because the search index also returns accessories; Lidl France uses the public product sitemap; Action France currently returns coolers/fans and is allowed to succeed with zero real air conditioners. Boulanger is deferred due Azure read timeout despite local accessibility.

## Conrad status

Conrad.nl is intentionally not registered. Its storefront and robots endpoint return Cloudflare HTTP 403 to the project's normal browser identity from local and Azure execution. Conrad's Developer Portal offers an official Price & Availability API and explicitly supports stock-monitoring use cases, but access is request-gated. Do not bypass the anti-bot layer.

A Developer Portal registration attempt on 2026-07-03 was rejected with "your email has not been allowlisted." A concise allowlist request was submitted the same day via the official Conrad contact form (developer.conrad.com/contact), addressed to `Conrad API Team`, requesting `Price & Availability` API access for a private non-commercial NL portable-airco stock alert (~5,000 calls/month, no orders/customer data). Status: awaiting Conrad's response. No adapter exists yet; implement one only after the email is allowlisted, the app is approved, and current official documentation has been reviewed.

## Configuration and secret model

- `MAX_PRICE_EUR=1500`
- `MIN_BTU=7000`
- `COUNTRIES=nl,fr` in production (comma-separated country codes; code default remains `nl`; drives `load_adapter_specs` in the registry)
- `EMAIL_LANG=zh` in production (`zh`, `nl`, and `en` are supported)
- The production recipient is stored as Key Vault secret `notification-email`.
- GitHub stores only `KEY_VAULT_SECRET_MAP=EMAIL_TO=notification-email`; it does not store the address.
- GitHub Actions variables (both repos): `AZURE_RESOURCE_GROUP=airco-tracker-rg`, `AZURE_CLIENT_ID=8adc0579-710f-4fcb-8762-28cea100a8a9` (deployer UAMI clientId, recreated 2026-07-05), `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, plus backend-only `EMAIL_LANG` and `KEY_VAULT_SECRET_MAP`.
- Migration completed on 2026-07-02: the old GitHub `EMAIL_TO` variable was deleted, `EMAIL_LANG=zh` was set, and the temporary Key Vault Secrets Officer assignment was confirmed absent after the write.
- `scripts/configure-notification-email.sh` migrates the old GitHub value or prompts without echo, temporarily grants the signed-in user Key Vault Secrets Officer, writes the secret, removes the temporary role, and deletes the old GitHub variable.
- The Container Apps Managed Identity has Key Vault Secrets User and hydrates `EMAIL_TO` at runtime.
- SMTP credentials remain local-only in `.env`; Azure uses passwordless Communication Services.

## Azure identity and OIDC notes (post-2026-07-05 rebuild)

- Runtime UAMI `aircontrack-identity`: clientId `ee7911d7-5ab9-4332-b9cc-b97fcd85d5d8`, principalId `76de0df0-4e20-481e-b86b-0ba510ba0e10`. Has 5 roles: AcrPull (ACR), Storage Blob Data Contributor, Storage Table Data Contributor, Key Vault Secrets User, Communication and Email Service Owner.
- Deployer UAMI `airco-github-deployer`: clientId `8adc0579-710f-4fcb-8762-28cea100a8a9`, principalId `af52c703-d15d-49c1-869a-bfd92af0d447`. Has the custom role `Airco GitHub Deployer Minimal` on `airco-tracker-rg`; it no longer has resource-group Contributor.
- OIDC federated credentials on the deployer UAMI: `github-airco-tracking` (subject `repo:ProgrammerAsahi/airco-tracking:ref:refs/heads/main`) and `github-airco-tracking-web` (subject `repo:ProgrammerAsahi/airco-tracking-web:ref:refs/heads/main`).
- Both `infra/github-oidc.bicep` files generate the federated-credential name as `github-${last(split(githubRepository, '/'))}`, producing readable names (`github-airco-tracking`, `github-airco-tracking-web`) that match the deployed credentials. The backend `infra/github-oidc.bicep` also creates/updates the subscription-scope `Airco GitHub Deployer Minimal` custom role via `infra/github-deployer-role.bicep`, then assigns it at resource-group scope with bicep's `guid()` for an idempotent name. Re-running `bootstrap-github-oidc.sh` is safe — it updates existing resources in place rather than creating duplicates or reintroducing Contributor.
- EmailService sender: `DoNotReply@a6522f3e-09c8-4ba0-a951-377b3c2b9c1b.azurecomm.net` (Job env `EMAIL_FROM`). The old sender `DoNotReply@65f5b17b-...azurecomm.net` is gone with the deleted EmailService.

## Frontend consumer and cross-repository contract

- Frontend local path: `~/airco-tracking-web`.
- The backend is the sole producer of private `airco-tracker/inventory.json`; current schema version is `1`.
- The frontend Container App serves the glacier-blue React dashboard and a same-origin `/api/inventory` endpoint. Its Node service reads this Blob with the existing runtime Managed Identity and caches reads for 30 seconds.
- The frontend reads the full private snapshot and filters it client-side by delivery destination. Delivery destination is URL state (`/deliver-to/nl`, `/deliver-to/fr`); display language is independent query/user preference state (`?lang=en`, language switcher/localStorage).
- The frontend reuses this project's Container Apps Environment, ACR, Storage Account, resource group, and runtime identity. Only a new `airco-tracking-web` Container App and repository-specific GitHub OIDC federated credential were added; no new database, Function App, Storage Account, environment, registry, Key Vault, or secret was created.
- The Blob container remains private. Never replace the frontend API with direct browser access, a public container, Storage Key, connection string, or long-lived SAS token.
- Backend producer: `airco_tracker/inventory.py`; persistence: `airco_tracker/inventory_store.py`.
- Frontend validator: `~/airco-tracking-web/server/inventory.ts`; browser types: `src/types.ts`; fixture: `test-fixtures/inventory.sample.json`.
- `delivery_coverage` is site-level metadata, not product-level metadata. The 2026-07-06 NL delivery audit is intentionally conservative: only official policy-page evidence widened coverage beyond `["nl"]`. Current widened entries are Kampeerwereld (`nl`,`be`), Vrijbuiter (`nl`,`be`,`de`; large Extra@Home deliveries are more limited and should be revisited if product-level delivery coverage is added), Solago (`nl`,`be`), and Airco-Webwinkel (`nl`,`be`,`lu`,`de`). Country-selector footers on shops such as Alternate, Create, Evolar, and Klarstein were not treated as cross-border evidence because they can point to separate storefronts with different inventory.
- A schema or semantics change must update and verify both repositories together: backend producer/tests, frontend validator/tests, browser types/UI, fixture, README, and both handoffs. Bump the schema version for breaking changes instead of silently reinterpreting version `1`.
- The frontend first production deployment succeeded in GitHub Actions run `28681867269`, scaled 0–2 replicas, and verified 27 sites / 15 available products from the live private Blob at that moment. Counts are time-sensitive.

## AliExpress external status

- Affiliate account approved on 2026-07-01.
- Open Platform developer type: `Dropshipping/Affiliates Developer` → `Affiliates (individual)`.
- API application submitted on 2026-07-01.
- Last observed status: `Under Review`, estimated 2–5 working days. The window has elapsed as of 2026-07-05; re-check the portal before starting work.
- No AliExpress adapter or secret configuration exists yet.
- Use only official Affiliate/Open Platform APIs after approval; never retain buyer, order, payment, or other personal data.
- After approval: inspect the app/key page without copying an App Secret into chat or source control; verify signing, product-search, availability, and Dutch-delivery fields from current official docs; implement a disabled-by-default adapter with synthetic response tests; store credentials in Key Vault via a hidden-input script; run the full unit suite and live dry-run before enabling production.

## Sites evaluated and not implemented

The following sites from the 2026-07-03 evaluation were investigated and excluded:

| Site | Reason |
|------|--------|
| De Wit Schijndel | Anubis anti-bot challenge blocks automated access. |
| Fritz Berger NL | Connection timeout from both local and Azure; site may be down or geo-blocked. |
| vidaXL NL | No real portable airco products in sitemap (only air-conditioner covers). |
| VEVOR NL | Products are fully JS-rendered; JSON-LD contains only category names with null offers; sitemap has no product URLs. |
| Hornbach NL | Only fixed split-airco units in sitemap; no portable air conditioners. |
| Intratuin | Sitemap endpoint returns a JPEG image; no airco category or product links found. |
| BCC | Price comparison/marketplace model ("Naar webshop" redirects to external shops); not a direct seller. |
| Klium | Cloudflare HTTP 403. |
| Qlima | Cloudflare HTTP 403; brand site, not a direct seller. |
| Electrogigant | Connection timeout. |
| Euronics | Connection timeout. |
| BAUHAUS NL | 404 on airco category; no portable airco found. |
| Fonq | Search returns no airco products. |

## Next expansion candidates

All Dutch candidates from the 2026-07-03 evaluation have been implemented and deployed:

| Site | Status | Implementation |
|------|--------|----------------|
| Costway NL | ✅ Deployed | Magento category page; `qty-N` photo class for stock. |
| Evolarshop | ✅ Deployed | Public Nosto GraphQL search API; excludes hoseless units. |
| Airco voor in huis | ✅ Deployed | WooCommerce product grid; `instock`/`outofstock` class. |
| Solago | ✅ Deployed | Shopify JSON-LD; pre-order text overrides InStock schema. |
| Hubo | ✅ Deployed | Shopify product sitemap + JSON-LD detail pages. |
| Vrijbuiter | ✅ Deployed | Category links + @graph JSON-LD; portable split units tracked. |
| Klimaatshop | ✅ Deployed | Custom category grid; `data-url` attribute + `.stock` span. |
| Airco-Webwinkel | ✅ Deployed | WooCommerce store discovered via product sitemap; JSON-LD detail pages. |
| Bostools | ✅ Deployed | WooCommerce mobile/caravan categories; dated presale and VAT-inclusive prices. |

Not recommended (do not implement):
- Vergelijkeven, Kieskeurig: price-comparison aggregators, second-hand stock data, not authoritative sellers. Kieskeurig also returns Vercel 429.
- RS Online: industrial B2B, search returns 403, low portable-airco value for integration cost similar to Conrad.

All previously listed NL candidates have been investigated and resolved. The NL retailer list is considered complete for the current scope.

## Next concrete steps

1. **France 403/API backlog** — investigate stable official/public data paths for Leroy Merlin, Darty, ManoMano, Fnac, Carrefour, Cdiscount, and E.Leclerc before adding them. Do not deploy adapters that only see anti-bot challenges or client-only shells.
2. **Conrad API** — still awaiting allowlist response; check the Developer Portal before starting work.
3. **AliExpress API** — approval window has elapsed; re-check the portal status before starting work.

## Development history (compact)

- **2026-07-06**: Added the stable France MVP batch (10 active adapters), wired `COUNTRIES=nl,fr` for production, added French presale markers and FR parser tests. Boulanger parser code is retained but deferred after production Azure verification showed consistent 60-second read timeouts. Backend tests: 104.
- **2026-07-06**: Hardened the post-rename architecture: added `country`/`site_id`, country-scoped state keys with legacy fallback, immediate vs presale counts, site-level `delivery_coverage`, presale-to-immediate alerts, explicit UAMI credential helper, Blob→Table endpoint derivation, and a `countries` Bicep parameter. Backend tests: 90.
- **2026-07-05**: Renamed `airco-tracking-nl` → `airco-tracking`; moved 27 adapters into `adapters/nl/`; added `registry.py` with `load_adapter_classes(countries)`; fixed `i18n_local.json` packaging; consolidated all Azure resources into `airco-tracker-rg` (old `airco-tracker-nl-rg` deleted); recreated UAMIs + EmailService with new clientIds; fixed OIDC bicep federated-credential name to be idempotent. Backend tests: 74. (commits `afdde97`, `db7cda6`)
- **2026-07-05**: Added Bostools as retailer 28 (WooCommerce mobile/caravan categories; dated presale; VAT-inclusive prices). (commit `6e50bf4`)
- **2026-07-04**: Frontend retailer detail page, presale tabs, and zh/nl/en localization (CSP-safe inert JSON). (frontend commits `d8fcc49`, `5d022fc`)
- **2026-07-03**: Expanded from 19 → 27 retailers (Costway, Evolarshop, Airco voor in huis, Solago, Hubo, Vrijbuiter, Klimaatshop, Airco-Webwinkel); BTU accuracy audit; live inventory snapshot `inventory.json`; public dashboard repository created. (commits `4d12719`, `ee33e59`, `3ce87c2`, `13e31ef`)
- **2026-07-02**: Azure Key Vault recipient storage; bol.com removal; EUR 1,500 / 7,000 BTU alert filters.
- Prior runs retained in git history.

## Standard local verification

```bash
cd ~/airco-tracking
.venv/bin/pip install --no-deps --force-reinstall .   # after code changes
.venv/bin/python -m unittest discover -v              # 90 tests
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
git diff --check
.venv/bin/python -m airco_tracker check --dry-run      # live network, no writes
```

For inventory contract changes, also verify the frontend (see `~/airco-tracking-web/HANDOFF.md`).

## Deployment commands (when authorized)

```bash
# Backend: push to main triggers .github/workflows/deploy.yml automatically.
# Manual deploy (skips CI, uses current HEAD):
IMAGE_TAG=$(git -C ~/airco-tracking rev-parse --short=12 HEAD) \
  AZURE_RESOURCE_GROUP=airco-tracker-rg \
  ~/airco-tracking/scripts/deploy-application.sh

# Frontend: push to main triggers .github/workflows/deploy.yml automatically.
# Manual deploy:
IMAGE_TAG=$(git -C ~/airco-tracking-web rev-parse --short=12 HEAD) \
  AZURE_RESOURCE_GROUP=airco-tracker-rg \
  ~/airco-tracking-web/scripts/deploy.sh

# Trigger a verification job execution:
az containerapp job start -n airco-tracker-job -g airco-tracker-rg

# Production API check:
curl -s https://airco-tracking-web.livelystone-5966d837.westeurope.azurecontainerapps.io/api/inventory | python3 -m json.tool | head
```

## Safeguards and known behaviour

- Retailers are isolated; one failure does not stop successful checks.
- State is updated only for retailers that completed successfully.
- Live inventory is independent from alert state. A successful site replaces its current available list; a failed site retains the last successful list with `status: error` and `stale: true`.
- Inventory snapshots do not apply price, BTU, or brand alert filters. Adapter-level scope rules still exclude air coolers, fans, accessories, and fixed split systems.
- The snapshot is a production cross-repository contract. Keep schema version `1` compatible with the deployed frontend, or coordinate an explicit version bump and dual-repository deployment.
- The inventory Blob must remain private; only Azure identities may read it directly. Public browser access goes through the frontend's same-origin API.
- Production saves inventory before validating/sending email. An email failure therefore leaves inventory fresh but alert state uncommitted, so the notification retries next run.
- `--dry-run` reads but writes neither inventory nor alert state.
- Unknown price or BTU is retained to avoid false negatives; known values are filtered at EUR 1,500 and 7,000 BTU.
- Category-based adapters fetch a product detail page only when the item is currently available and its BTU is unknown. This keeps the ten-minute scan light while preventing an available low-capacity item from bypassing `MIN_BTU`.
- Explicitly labelled cooling capacity in W/kW may be converted to BTU; generic input power or electricity-consumption figures must never be converted. Verified model fallbacks cover ArcticMove, Qlima, COMFEE, and current Trotec PAC models when retailer cards omit units.
- Air coolers, fans, dehumidifiers, window kits, hoses, and other accessories must not alert.
- Multi-week lead times, presales, store-only stock, and collection-only stock are unavailable.
- A non-dry production check validates email configuration after saving inventory and before reading/updating alert state, so a missing Key Vault recipient still fails deployment verification without making the inventory stale.
- `doctor` reports whether the recipient is configured without printing the address.

## Verification snapshot

- Unit tests: 90 passed, including the rewritten `test_cli.py` (patches `load_adapter_specs` instead of 28 individual adapter names), registry duplicate-site and delivery-coverage validation, Bostools stock/presale/price semantics, inventory filter separation, immediate/presale counts, successful-empty replacement, failed-site retention, country-scoped state keys, presale-to-immediate alerts, local-store round trip, dry-run no-write, and email-failure ordering.
- Shell syntax: clean.
- `git diff --check`: clean.
- Live local dry-run on 2026-07-05 (post-rename): all 28 retailers completed via the registry path; the snapshot preview contained 21 available products and the alert filter selected 12.
- Live local dry-run on 2026-07-05 (pre-rename): all 28 retailers completed; the snapshot preview contained 20 available products and the alert filter selected 12. Bostools returned 6 in-scope air conditioners, with its one available PortaSplit retained as `presale=true` and therefore excluded from email alerts.
- Live local dry-run on 2026-07-03: all 27 retailers completed; snapshot preview contained 19 available products and the alert filter selected 13. Known low-BTU and over-EUR-1,500 products remained in the snapshot but were excluded from alerts.
- Rename + registry deployment: Actions run `28745071912` for commit `afdde97`: succeeded in 3m59s. Verification execution `airco-tracker-job-ftzu1v6`: Succeeded. OIDC login succeeded with the updated subject `repo:ProgrammerAsahi/airco-tracking:ref:refs/heads/main`. Production API verified 2026-07-05T15:14Z: 28 sites, 20 available products, 0 stale sites. Frontend verify script passed: 28 sites, 20 available products.
- First deploy run `28744264341` for commit `59e58bc` failed its verification execution because of a pre-existing `i18n_local.json` packaging bug: when the Azure Table Storage i18n query failed, the local fallback raised `FileNotFoundError`. Fixed in `afdde97` by adding `[tool.setuptools.package-data]` for `i18n_local.json`; the second deploy succeeded.
- Delivery coverage deployment 2026-07-06: backend Actions run `28789725432` for commit `352338c` succeeded in 4m24s; frontend Actions run `28789724133` for commit `d787664` succeeded in 2m41s. Production images: `airco-tracker:352338c02f5ee9b868766cb973e00ccc762245f4` and `airco-tracking-web:d78766428dd017e4fb31b7a4cb74ed3c5e60ae4d`. Production API verified `2026-07-06T12:01:31.736406+00:00`: 28 sites, 22 available products (12 immediate, 10 presale), 0 stale, `delivery_coverage` present on all sites; widened values confirmed for Kampeerwereld (`be`,`nl`), Solago (`be`,`nl`), Vrijbuiter (`be`,`de`,`nl`), and Airco-Webwinkel (`be`,`de`,`lu`,`nl`). Frontend deployment verifier passed against the live URL.
- Production image: `aircotrackertdzvfmmi.azurecr.io/airco-tracker:352338c02f5ee9b868766cb973e00ccc762245f4` (full SHA tag).
- Scheduled job executions after deploy: `29721060`, `29721070` both Succeeded.
- GitHub deployer least-privilege hardening 2026-07-06: replaced the deployer UAMI's resource-group Contributor assignment with the custom role `Airco GitHub Deployer Minimal`. The first custom-role-only workflow test exposed the need for `Microsoft.App/managedEnvironments/join/action`; after adding that action, both backend and frontend `workflow_dispatch` deployments succeeded with only the custom role. The role is now codified in `infra/github-deployer-role.bicep` and assigned by `infra/github-oidc.bicep`.
- OIDC bicep fix 2026-07-05: changed `infra/github-oidc.bicep` (both repos) from `uniqueString()` to `last(split(githubRepository,'/'))` so the federated-credential name matches the deployed credentials (`github-airco-tracking`, `github-airco-tracking-web`). Redeployed both bicep files; the deployer role assignment was also recreated via bicep's `guid()` for idempotency. Re-running `bootstrap-github-oidc.sh` is now safe. (commits `db7cda6` backend, `f150a2b` frontend)
- Azure resource move 2026-07-05: created `airco-tracker-rg` and moved 9 resources (Storage, ACR, KeyVault, LogAnalytics, ACS, ManagedEnvironment, ContainerApp, Job) from `airco-tracker-nl-rg`. The 2 UAMIs and EmailService (not movable) were recreated in the new RG: runtime UAMI new clientId `ee7911d7-5ab9-4332-b9cc-b97fcd85d5d8`, deployer UAMI new clientId `8adc0579-710f-4fcb-8762-28cea100a8a9`, new EmailService sender `DoNotReply@a6522f3e-...azurecomm.net`. Recreated 6 role assignments (5 runtime + 1 deployer) and 2 OIDC federated credentials (`github-airco-tracking`, `github-airco-tracking-web`). Redeployed `job.bicep` and `app.bicep` to update identity references and `EMAIL_FROM`/`AZURE_CLIENT_ID` env. ACS `linkedDomains` updated to the new Domain. Old `airco-tracker-nl-rg` and all its resources deleted. Manual job execution `airco-tracker-job-e9q0tl4`: Succeeded. Scheduled execution `29721200` post-deletion: Succeeded. Frontend API verified 2026-07-05T17:21Z: 28 sites, 19 available products, 0 stale sites. GitHub Actions `AZURE_RESOURCE_GROUP` and `AZURE_CLIENT_ID` variables updated on both repos.
- GitHub Actions run `28670790535` for commit `13e31ef`: succeeded in 3m57s. Verification execution `airco-tracker-job-d0zpn59`: Succeeded.
- Production created `airco-tracker/inventory.json` via Managed Identity (HTTP 201): 13,830 bytes, 27 sites, 19 available products, 0 stale sites. No email was sent because alert state contained no new restocks.
- Production image: `aircotrackertdzvfmmi.azurecr.io/airco-tracker:13e31efde353c649703abe853afb5d4f5a4ac783`.
- Frontend localization repair deployment: Actions run `28717820865`, image `airco-tracking-web:5d022fc45e9e9d03bec567cd6afaee5f59e37f90`, and the strengthened verifier passed strict CSP, three-language inert JSON, and inventory checks. Live API verification returned 27 sites / 20 available products / 0 stale sites at `2026-07-04T19:54:00Z`; production browser QA passed Chinese, Dutch, and English switching.
- Bostools deployment: Actions run `28735561062` for backend commit `6e50bf4`: succeeded in 4m13s. Actions run `28735567922` for frontend commit `069f587`: succeeded in 2m42s. Production API verified 2026-07-05: 28 sites, 20 available products, 0 stale sites. Bostools returned 1 presale product (Midea PortaSplit, €1,290, 12,000 BTU, `presale=true`), correctly excluded from email alerts. Backend image: `aircotrackertdzvfmmi.azurecr.io/airco-tracker:6e50bf4eed852f909060ee95ff7bd234c070c621`. Frontend image: `aircotrackertdzvfmmi.azurecr.io/airco-tracking-web:069f587e0cc84b7f1c82d3e04020c71e8b5c38d2`.
- Prior runs retained in git history.
- Expected per-product warnings remain for one retired Obelink URL, two Kampeerwereld URLs returning HTTP 410, and one De'Longhi product missing JSON-LD offer. Their adapters still completed successfully; these warnings do not mark the retailer check as failed.

## Updating this handoff

Replace stale status instead of appending a diary. Always record the deployed commit, active retailer count, external API review state, frontend contract compatibility, exact verification evidence, and next concrete action. Never include email addresses, secret values, tokens, passwords, or unnecessary personal information.

## i18n architecture

Multi-language support (zh/nl/en) is backed by Azure Table Storage:
- Table "i18n" in the existing Storage Account stores 44 entries (11 email + 33 web), each with `zh`, `nl`, `en` columns.
- `i18n_table.py` loads from Table Storage via Managed Identity; `i18n_local.json` is the local fallback.
- `i18n.py` uses dynamic loading; `translate()` API unchanged.
- `foundation.bicep` includes the Storage Table Data Contributor role.
- `scripts/seed-i18n.py` is a one-time seeding script (already run).
- `i18n_local.json` is now packaged via `[tool.setuptools.package-data]` so the local fallback works inside the Docker image. Previously it was missing from `site-packages`, so a Table Storage outage crashed the job with `FileNotFoundError`.
- Frontend reads the `web` partition through Managed Identity; translations are injected as CSP-safe inert `application/json` (not executable inline script). The frontend bug where raw key names appeared instead of translated text was caused by the strict `script-src 'self'` CSP blocking the original inline `window.__I18N__` assignment; this is resolved (frontend commit `5d022fc`).

## Adapter registry architecture (2026-07-05)

The 28 Dutch retailer adapter modules live in `airco_tracker/adapters/nl/`; the first 11 French adapter modules live in `airco_tracker/adapters/fr/`, with 10 currently active and Boulanger deferred. Country-agnostic parsing helpers (`base.py` with the `Adapter` ABC and price/BTU/presale parsing, `schema.py` with JSON-LD helpers, `sitemap.py` with sitemap discovery) remain at `airco_tracker/adapters/` top level.

- `adapters/nl/__init__.py` exports the 28 adapter classes and defines `ADAPTERS` (ordered list, matching the previous `cli.py` runtime order).
- `adapters/fr/__init__.py` exports the France MVP adapter classes, defines active `ADAPTERS` for `COUNTRIES=fr`, and keeps Boulanger in `DEFERRED_ADAPTERS` until its production fetch path is stable.
- `adapters/registry.py` aggregates `ADAPTERS` by country into `_ADAPTERS_BY_COUNTRY`, exposes `load_adapter_specs(countries)`, and fail-fast validates duplicate `site_id` values and malformed delivery-coverage tokens so same-country adapters cannot silently overwrite each other or publish unusable country filters.
- `cli.py` calls `load_adapter_specs(config.countries)` and instantiates the spec-bound classes; it no longer imports the 28 adapter names. Runtime products are stamped with the adapter country and stable `site_id`.
- `config.py` reads `COUNTRIES` (default `nl`, comma-separated) into `Config.countries`.
- Adding a country: create `adapters/<cc>/__init__.py` with an `ADAPTERS` list, register it in `registry._ADAPTERS_BY_COUNTRY`, add conservative `_DELIVERY_COVERAGE_BY_SITE_ID` entries, and deploy `countries=nl,<cc>` / `COUNTRIES=nl,<cc>`. No `cli.py` or `test_cli.py` changes needed.
- `test_cli.py` patches `airco_tracker.cli.load_adapter_specs` to inject fake adapter specs instead of patching 28 individual names on the `cli` module.

### Adding a country (checklist)

1. Create `airco_tracker/adapters/<cc>/` with one module per retailer (copy an existing NL adapter as a template; use `from ...models import Product`, `from ..base import ...` — three dots to reach `airco_tracker.models`, two dots to reach `adapters.base`).
2. In `airco_tracker/adapters/<cc>/__init__.py`, import each adapter class and define `ADAPTERS = [...]` (ordered list). Do not export internal base classes.
3. In `airco_tracker/adapters/registry.py`, add `from .<cc> import ADAPTERS as _<cc>_ADAPTERS`, register `_ADAPTERS_BY_COUNTRY["<cc>"] = _<cc>_ADAPTERS`, and add conservative `_DELIVERY_COVERAGE_BY_SITE_ID` entries for every new `site_id`.
4. Add parser tests in `tests/test_parsers.py` using `from airco_tracker.adapters.<cc>.<module> import ...`.
5. Set `COUNTRIES=nl,<cc>` in production: pass the `countries` param in `infra/job.bicep` or update the Job env via `az containerapp job update`. The `Config.countries` field reads the `COUNTRIES` env var (comma-separated, default `nl`).
6. Run `.venv/bin/python -m unittest discover -v` and `.venv/bin/python -m airco_tracker check --dry-run`; confirm the new country's retailers appear in the snapshot.
7. `cli.py` and `test_cli.py` do not change — the registry handles discovery automatically.
