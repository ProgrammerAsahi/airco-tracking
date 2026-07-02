# Airco Tracker NL — current handoff

Last updated: 2026-07-02 (Europe/Amsterdam)

## Current objective

Expand reliable portable-air-conditioner coverage for Dutch delivery while keeping credentials out of source control. The immediate pending task is the official AliExpress Affiliate/Open Platform integration after API approval.

## Repository and production state

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking-nl`
- Branch: `main`
- Last deployed commit: `ce0aa11` (`Restore executable mode on deploy scripts`; application code changes from `781ebc9`)
- GitHub workflow: `Deploy to Azure`
- Azure resource group: `airco-tracker-nl-rg`
- Azure Container Apps job: `airco-tracker-job`
- Schedule: every 10 minutes
- State: Azure Blob Storage (`airco-tracker/state.json`)
- Notifications: Azure Communication Services Email
- Secrets: Azure Key Vault through Managed Identity

## Recently completed (2026-07-02)

Systematic review and repair of accuracy, maintainability, i18n, tests, and infrastructure:

- **Wehkamp adapter**: removed erroneous `monoblock` exclusion (monoblock is genuine portable form factor); added multi-week lead-time detection (`"weken"`) so preorders do not trigger false alerts.
- **Praxis adapter**: added positive keyword requirement (`airco`/`airconditioning`/`aircondition`) to `_is_portable_airco`, previously relied solely on negative exclusions; added watt→BTU fallback for titles that state cooling capacity in watts (e.g. `MPPD-12 3500W` → ~11942 BTU) so `MIN_BTU` filtering still applies when no BTU figure is present.
- **Lidl adapter**: replaced self-implemented JSON-LD parser with shared `schema.py` functions (`product_json_ld`/`first_offer`/`offer_price`/`schema_in_stock`), fixing `@graph` support gap and removing duplication.
- **ElectroWorld**: `_positive_int` threshold corrected from `>=0` to `>0` to match semantics.
- **Fetcher**: User-Agent version now reads from package metadata (`importlib.metadata`) instead of hardcoded `0.1`.
- **Email i18n**: new `airco_tracker/i18n.py` with zh/nl/en translations; `EMAIL_LANG` config (default `zh`); `mailer.py` uses `translate()`; `job.bicep`, `.env.example`, and deploy scripts updated; three READMEs synced.
- **Tests**: added `tests/test_fetch.py` (5 tests), dry-run safety assertions in `tests/test_cli.py` (2 tests), multilingual email test in `tests/test_cloud_backends.py`, and 4 new parser tests (Wehkamp lead-time/monoblock, Lidl @graph, Praxis watt→BTU). Total: 38 tests.
- **Deploy verification**: `scripts/deploy-application.sh` now waits for the Container Apps job execution result and exits non-zero on failure.
- **Infrastructure**: Key Vault `enablePurgeProtection` enabled (irreversible). `softDeleteRetentionInDays` remains at 7 days — Azure does not allow modifying this property after creation, so the planned 90-day extension could not be applied. Communication Owner and OIDC Contributor roles retained as-is (documented as acceptable: ACS data-plane RBAC is hard to verify; OIDC needs Contributor for deployment).
- **Hygiene**: personal email address replaced with `you@example.com` placeholders in deploy scripts, test fixtures, and all three READMEs. Version bumped to `0.7.0`.

Not changed (documented decisions):
- 6 sitemap/API adapters do not inherit `Adapter` base class — pure style issue, no functional impact, refactor risk exceeds benefit.
- Communication Services Owner role and GitHub OIDC Contributor role retained (see above).

## Supported retailers

Active without private credentials:

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

Optional/credential-gated:

- bol.com: official Marketing Catalog API adapter exists but production remains disabled until official API credentials are configured. Do not restore webpage scraping; Azure IPs receive 403 and the search route is robots-restricted.
- AliExpress: no adapter has been implemented yet. Use the official Affiliate/Open Platform API only.

## AliExpress external status

- Affiliate account approved on 2026-07-01.
- Open Platform developer type selected: `Dropshipping/Affiliates Developer` → `Affiliates (individual)`.
- API application submitted on 2026-07-01.
- Current portal status: `Under Review` with an estimated review time of 2–5 working days.
- Intended data scope: public affiliate product catalog/offer data only (title, URL, price, availability, promotion/tracking link, and minimal API metadata).
- Do not request or retain buyer, order, payment, or other personal data.
- Runtime processing is hosted on Microsoft Azure. Core compute/storage is Azure West Europe; Azure Communication Services is configured with the Europe data location.

## Next steps after AliExpress approval

1. Ask the user to open the API page and identify the approved app/key screen. Do not request screenshots containing an App Secret.
2. Verify the current official Affiliate API authentication/signing and product-search endpoints from primary AliExpress documentation.
3. Design an `AliExpressAdapter` that searches portable/compressor air conditioners, filters out air coolers/accessories, enforces Dutch deliverability when the API exposes it, and uses the existing `MAX_PRICE_EUR`/`MIN_BTU` alert filters.
4. Add configuration fields and validation with a disabled-by-default backend, similar to bol.com.
5. Add a hidden-input setup script (for example `scripts/configure-aliexpress-api.sh`) that writes secrets directly to Azure Key Vault and configures only non-sensitive GitHub Actions variables.
6. Add parser/API tests using synthetic responses; never put real credentials or captured private responses in fixtures.
7. Run the full test suite and local live dry-run, then deploy through the existing GitHub Actions pipeline if the user authorizes it.
8. Confirm the production image SHA and inspect one Container Apps job execution for the AliExpress retailer count.

## Known behavior and safeguards

- Retailers are isolated: one failure does not stop other checks.
- Missing seasonal products are marked unavailable only when that retailer completed successfully, allowing a later restock transition without treating a failed request as stock loss.
- Trotec multi-week lead times are not immediate stock.
- Action expired deals are kept as known URLs so reactivation can be detected.
- All adapters must exclude air coolers, fans, and accessories.
- Production credentials must remain in Key Vault; configuration maps environment variable names to secret names.

## Verification snapshot

- Unit tests after this repair round: 38 passed (was 26).
- Live local dry-run after commit `781ebc9`: all 14 retailers ran without errors. Counts: Coolblue 11/0, MediaMarkt 5/1, EP 7/0, Electro World 3/0, Wehkamp 1/1, Lidl 5/0, GAMMA 3/0, KARWEI 2/0, Praxis 9/1, Alternate 0/0, Trotec 13/0, Klarstein 18/0, FlinQ 2/0, Action 1/0.
- Live local dry-run on 2026-07-02 (post watt→BTU fix): all 14 retailers ran without errors. Praxis watt→BTU confirmed on `MPPD-12 3500W` → btu=11942. European heat wave has driven most retailers to 0 available stock, which is expected.
- GitHub Actions deployment run `28585347734` for commit `ce0aa11`: succeeded. Deploy verification execution `airco-tracker-job-utl4kwg`: succeeded.
- Foundation Bicep deployment: succeeded. Key Vault purge protection enabled (verified via `az keyvault show`).

## Updating this handoff

Replace stale status rather than appending a diary. Always update the date, deployed commit, external review state, next concrete action, and verification evidence. Never include secret values, tokens, passwords, or unnecessary personal information.
