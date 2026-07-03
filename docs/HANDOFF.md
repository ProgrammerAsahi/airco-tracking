# Airco Tracker NL — current handoff

Last updated: 2026-07-03 (Europe/Amsterdam)

## Current objective

Run a reliable, low-maintenance portable-air-conditioner stock tracker for delivery to Dutch addresses. Production runs every ten minutes in Azure and sends an email only for first-seen or newly-restocked products.

The current development round adds five active retailers, migrates the notification recipient from GitHub configuration to Azure Key Vault, standardises the filters at EUR 1,500 and 7,000 BTU, and removes an unusable retailer integration. Conrad remains pending because its public pages reject automated requests and its official API requires separate approval.

## Repository and production

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking-nl`
- Branch: `main`
- Feature commit: `4bbb990f1c0e2a8e9b6429cbcae0a58b1973b159`
- Last verified production image: commit `4bbb990f1c0e2a8e9b6429cbcae0a58b1973b159`
- GitHub workflow: `Deploy to Azure`
- Azure resource group: `airco-tracker-nl-rg`
- Container Apps job: `airco-tracker-job`
- Schedule: `*/10 * * * *` (UTC)
- State: Azure Blob Storage, `airco-tracker/state.json`
- Notifications: Azure Communication Services Email
- Runtime identity: user-assigned Managed Identity

## Active retailers

The application currently registers 19 credential-free adapters:

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

New-retailer stock semantics:

- Expert: only explicit online saleability counts; local-store-only stock is unavailable.
- De'Longhi: product JSON-LD is authoritative, but `Breng mij op de hoogte` forces unavailable.
- Obelink and Kampeerwereld: known seasonal URLs remain checked after products disappear from category pages.
- Kampeerwereld: `Exclusief in winkel` never counts as deliverable.
- Create: `Presale` and future `Verzending vanaf` dates are unavailable.

## Conrad status

Conrad.nl is intentionally not registered. Its storefront and robots endpoint return Cloudflare HTTP 403 to the project's normal browser identity from local and Azure execution. Conrad's Developer Portal offers an official Price & Availability API and explicitly supports stock-monitoring use cases, but access is request-gated. Do not bypass the anti-bot layer. The next valid step is to request official API access and implement an adapter only after credentials and current official documentation are available.

## Configuration and secret model

- `MAX_PRICE_EUR=1500`
- `MIN_BTU=7000`
- `EMAIL_LANG=zh` in production (`zh`, `nl`, and `en` are supported)
- The production recipient is stored as Key Vault secret `notification-email`.
- GitHub stores only `KEY_VAULT_SECRET_MAP=EMAIL_TO=notification-email`; it does not store the address.
- Migration completed on 2026-07-02: the old GitHub `EMAIL_TO` variable was deleted, `EMAIL_LANG=zh` was set, and the temporary Key Vault Secrets Officer assignment was confirmed absent after the write.
- `scripts/configure-notification-email.sh` migrates the old GitHub value or prompts without echo, temporarily grants the signed-in user Key Vault Secrets Officer, writes the secret, removes the temporary role, and deletes the old GitHub variable.
- The Container Apps Managed Identity has Key Vault Secrets User and hydrates `EMAIL_TO` at runtime.
- SMTP credentials remain local-only in `.env`; Azure uses passwordless Communication Services.

## AliExpress external status

- Affiliate account approved on 2026-07-01.
- Open Platform developer type: `Dropshipping/Affiliates Developer` → `Affiliates (individual)`.
- API application submitted on 2026-07-01.
- Last observed status: `Under Review`, estimated 2–5 working days.
- No AliExpress adapter or secret configuration exists yet.
- Use only official Affiliate/Open Platform APIs after approval; never retain buyer, order, payment, or other personal data.

## Next steps after AliExpress approval

1. Inspect the approved app/key page without copying an App Secret into chat or source control.
2. Verify signing, product-search, availability, and Dutch-delivery fields from current official documentation.
3. Implement a disabled-by-default API adapter with synthetic response tests.
4. Store credentials directly in Key Vault through a hidden-input setup script.
5. Run the full unit suite and live dry-run before enabling production.

## Safeguards and known behaviour

- Retailers are isolated; one failure does not stop successful checks.
- State is updated only for retailers that completed successfully.
- Unknown price or BTU is retained to avoid false negatives; known values are filtered at EUR 1,500 and 7,000 BTU.
- Category-based adapters fetch a product detail page only when the item is currently available and its BTU is unknown. This keeps the ten-minute scan light while preventing an available low-capacity item from bypassing `MIN_BTU`.
- Explicitly labelled cooling capacity in W/kW may be converted to BTU; generic input power or electricity-consumption figures must never be converted. Verified model fallbacks cover ArcticMove, Qlima, COMFEE, and current Trotec PAC models when retailer cards omit units.
- Air coolers, fans, dehumidifiers, window kits, hoses, and other accessories must not alert.
- Multi-week lead times, presales, store-only stock, and collection-only stock are unavailable.
- A non-dry production check validates email configuration before fetching retailers, so a missing Key Vault recipient fails deployment verification immediately.
- `doctor` reports whether the recipient is configured without printing the address.

## Verification snapshot

- Unit tests: 54 passed, including detail enrichment, labelled cooling-watt conversion, input-power rejection, known-model inference, and minimum-BTU rejection regressions.
- Shell syntax: clean.
- `git diff --check`: clean.
- Full live audit on 2026-07-03: 121 products across all 19 adapters, 22 products below 7,000 BTU, zero currently available products with unknown BTU, and zero low-BTU alerts. The only product page with no reliable capacity data is an unavailable retired Lidl TRONIC listing; it remains unknown rather than being guessed from price.
- Azure recipient migration: complete. Current Container Apps configuration contains no plain `EMAIL_TO`; it contains `EMAIL_LANG=zh`, `MIN_BTU=7000`, `MAX_PRICE_EUR=1500`, and `KEY_VAULT_SECRET_MAP=EMAIL_TO=notification-email`.
- GitHub Actions run `28649478128`: succeeded in 3m40s. Verification execution `airco-tracker-job-t7dsmpq`: Succeeded. Its logs show the two available low-capacity products as 5,118 and 3,200 BTU and end with `No new stock; no email sent`.
- Production image: `aircotrackertdzvfmmi.azurecr.io/airco-tracker:4bbb990f1c0e2a8e9b6429cbcae0a58b1973b159`; production `MIN_BTU` remains `7000`.
- Expected per-product warnings remain for one retired Obelink URL and two Kampeerwereld URLs returning HTTP 410. Their adapters still completed with 15 and 5 parsed products respectively; these warnings do not mark the retailer check as failed.

## Updating this handoff

Replace stale status instead of appending a diary. Always record the deployed commit, active retailer count, external API review state, exact verification evidence, and next concrete action. Never include email addresses, secret values, tokens, passwords, or unnecessary personal information.
