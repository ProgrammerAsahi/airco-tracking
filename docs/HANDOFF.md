# Airco Tracker NL — current handoff

Last updated: 2026-07-02 (Europe/Amsterdam)

## Current objective

Run a reliable, low-maintenance portable-air-conditioner stock tracker for delivery to Dutch addresses. Production runs every ten minutes in Azure and sends an email only for first-seen or newly-restocked products.

The current development round adds five active retailers, migrates the notification recipient from GitHub configuration to Azure Key Vault, standardises the filters at EUR 1,500 and 7,000 BTU, and removes an unusable retailer integration. Conrad remains pending because its public pages reject automated requests and its official API requires separate approval.

## Repository and production

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking-nl`
- Branch: `main`
- Working-tree baseline: `02cae9e`
- Last verified production image: commit `6930a24`
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
- Air coolers, fans, dehumidifiers, window kits, hoses, and other accessories must not alert.
- Multi-week lead times, presales, store-only stock, and collection-only stock are unavailable.
- A non-dry production check validates email configuration before fetching retailers, so a missing Key Vault recipient fails deployment verification immediately.
- `doctor` reports whether the recipient is configured without printing the address.

## Verification snapshot

- Unit tests: 44 passed after the five new parser fixtures, shared defaults test, Create deduplication test, and Python 3.9-compatible adapter patch helper were added.
- Shell syntax: clean.
- `git diff --check`: clean.
- Final installed-version dry-run: all 19 registered retailers completed. New-site counts were Expert 11/0, De'Longhi 11/0, Obelink 13/1, Kampeerwereld 5/1, and Create 2/0. The two available camping units were 5,100 and 3,200 BTU and were correctly filtered out; a EUR 1,999 MediaMarkt unit was also filtered out. Only the Wehkamp 7,000 BTU / EUR 225 product qualified for an alert.
- Azure recipient migration: complete. GitHub push and production execution verification remain for this working round; replace this line with concrete evidence after completion.

## Updating this handoff

Replace stale status instead of appending a diary. Always record the deployed commit, active retailer count, external API review state, exact verification evidence, and next concrete action. Never include email addresses, secret values, tokens, passwords, or unnecessary personal information.
