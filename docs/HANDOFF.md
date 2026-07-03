# Airco Tracker NL — current handoff

Last updated: 2026-07-03 (Europe/Amsterdam)

## Current objective

Run a reliable, low-maintenance portable-air-conditioner stock tracker for delivery to Dutch addresses. Production runs every ten minutes in Azure and sends an email only for first-seen or newly-restocked products.

The current development round expands retailer coverage from 19 to 23 credential-free adapters, adding Costway NL, Evolarshop, Airco voor in huis, and Solago. Prior rounds migrated the notification recipient to Azure Key Vault, standardised filters at EUR 1,500 and 7,000 BTU, removed the bol.com integration, and audited BTU capacity across all adapters. Conrad remains pending because its public pages reject automated requests and its official API requires separate approval.

## Repository and production

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking-nl`
- Branch: `main`
- Feature commit: `4d127198785fa09236954a9fdbd12848cf47ce01`
- Last verified production image: commit `4d127198785fa09236954a9fdbd12848cf47ce01`
- GitHub workflow: `Deploy to Azure`
- Azure resource group: `airco-tracker-nl-rg`
- Container Apps job: `airco-tracker-job`
- Schedule: `*/10 * * * *` (UTC)
- State: Azure Blob Storage, `airco-tracker/state.json`
- Notifications: Azure Communication Services Email
- Runtime identity: user-assigned Managed Identity

## Active retailers

The application currently registers 23 credential-free adapters:

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

## Conrad status

Conrad.nl is intentionally not registered. Its storefront and robots endpoint return Cloudflare HTTP 403 to the project's normal browser identity from local and Azure execution. Conrad's Developer Portal offers an official Price & Availability API and explicitly supports stock-monitoring use cases, but access is request-gated. Do not bypass the anti-bot layer.

A Developer Portal registration attempt on 2026-07-03 was rejected with "your email has not been allowlisted." A concise allowlist request was submitted the same day via the official Conrad contact form (developer.conrad.com/contact), addressed to `Conrad API Team`, requesting `Price & Availability` API access for a private non-commercial NL portable-airco stock alert (~5,000 calls/month, no orders/customer data). Status: awaiting Conrad's response. No adapter exists yet; implement one only after the email is allowlisted, the app is approved, and current official documentation has been reviewed.

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
- Last observed status: `Under Review`, estimated 2–5 working days. As of 2026-07-03 (day 3) the window has not elapsed; re-check the portal before starting work.
- No AliExpress adapter or secret configuration exists yet.
- Use only official Affiliate/Open Platform APIs after approval; never retain buyer, order, payment, or other personal data.

## Next steps after AliExpress approval

1. Inspect the approved app/key page without copying an App Secret into chat or source control.
2. Verify signing, product-search, availability, and Dutch-delivery fields from current official documentation.
3. Implement a disabled-by-default API adapter with synthetic response tests.
4. Store credentials directly in Key Vault through a hidden-input setup script.
5. Run the full unit suite and live dry-run before enabling production.

## Next expansion candidates

All four sites from the 2026-07-03 evaluation have been implemented and deployed:

| Site | Status | Implementation |
|------|--------|----------------|
| Costway NL | ✅ Deployed | Magento category page; `qty-N` photo class for stock. |
| Evolarshop | ✅ Deployed | Public Nosto GraphQL search API; excludes hoseless units. |
| Airco voor in huis | ✅ Deployed | WooCommerce product grid; `instock`/`outofstock` class. |
| Solago | ✅ Deployed | Shopify JSON-LD; pre-order text overrides InStock schema. |

Not recommended (do not implement):
- Vergelijkeven, Kieskeurig: price-comparison aggregators, second-hand stock data, not authoritative sellers. Kieskeurig also returns Vercel 429.
- RS Online: industrial B2B, search returns 403, low portable-airco value for integration cost similar to Conrad.

Worth investigating later: De Wit Schijndel, Vrijbuiter, Fritz Berger NL (camping/RV/split coverage); vidaXL NL, VEVOR NL (large catalog but need strict aircooler/accessory exclusion); Hornbach, Hubo, Intratuin (seasonal, may add little).

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

- Unit tests: 60 passed, including 6 new tests for Costway, Evolarshop, Airco voor in huis, and Solago adapters.
- Shell syntax: clean.
- `git diff --check`: clean.
- Live local dry-run on 2026-07-03 (post 4-retailer expansion): all 23 retailers ran without errors. Counts: Coolblue 11/0, MediaMarkt 4/1, EP 7/0, Electro World 1/0, Wehkamp 0/0, Lidl 6/0, GAMMA 3/0, KARWEI 2/0, Praxis 9/0, Alternate 0/0, Trotec 13/0, Klarstein 18/0, FlinQ 2/0, Action 1/0, Expert 11/0, De'Longhi 10/0, Obelink 15/2, Kampeerwereld 5/1, Create 2/0, Costway 15/4, Evolarshop 41/5, Airco voor in huis 6/1, Solago 1/0.
- 14 available products across all retailers; 7 passed MIN_BTU=7000 + MAX_PRICE_EUR=1500 filters. Low-BTU units (1800, 3200, 3207, 5118) and over-€1500 units correctly excluded from alerts.
- GitHub Actions run `28657889618` for commit `4d12719`: succeeded in 4m02s. Verification execution `airco-tracker-job-0jj1a7f`: Succeeded.
- Production image: `aircotrackertdzvfmmi.azurecr.io/airco-tracker:4d127198785fa09236954a9fdbd12848cf47ce01`.
- Prior run `28649478128` for commit `4bbb990`: succeeded. Earlier verification evidence retained in git history.
- Expected per-product warnings remain for one retired Obelink URL and two Kampeerwereld URLs returning HTTP 410. Their adapters still completed successfully; these warnings do not mark the retailer check as failed.

## Updating this handoff

Replace stale status instead of appending a diary. Always record the deployed commit, active retailer count, external API review state, exact verification evidence, and next concrete action. Never include email addresses, secret values, tokens, passwords, or unnecessary personal information.
