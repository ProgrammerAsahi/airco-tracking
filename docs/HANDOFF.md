# Airco Tracker NL — current handoff

Last updated: 2026-07-03 (Europe/Amsterdam)

## Current objective

Run a reliable, low-maintenance portable-air-conditioner stock tracker for delivery to Dutch addresses. Production runs every ten minutes in Azure and sends an email only for first-seen or newly-restocked products.

The current development round expands retailer coverage from 25 to 27 credential-free adapters, adding Klimaatshop and Airco-Webwinkel after a web search for additional Dutch airco retailers. Prior rounds added Hubo, Vrijbuiter, Costway NL, Evolarshop, Airco voor in huis, and Solago; migrated the notification recipient to Azure Key Vault; standardised filters at EUR 1,500 and 7,000 BTU; removed the bol.com integration; and audited BTU capacity across all adapters. Conrad remains pending because its public pages reject automated requests and its official API requires separate approval.

## Repository and production

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking-nl`
- Branch: `main`
- Feature commit: `3ce87c28a3b6e95e549415c0a5bb486d2eba7a66`
- Last verified production image: commit `3ce87c28a3b6e95e549415c0a5bb486d2eba7a66`
- GitHub workflow: `Deploy to Azure`
- Azure resource group: `airco-tracker-nl-rg`
- Container Apps job: `airco-tracker-job`
- Schedule: `*/10 * * * *` (UTC)
- State: Azure Blob Storage, `airco-tracker/state.json`
- Notifications: Azure Communication Services Email
- Runtime identity: user-assigned Managed Identity

## Active retailers

The application currently registers 27 credential-free adapters:

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

All four sites from the 2026-07-03 evaluation have been implemented and deployed:

| Site | Status | Implementation |
|------|--------|----------------|
| Costway NL | ✅ Deployed | Magento category page; `qty-N` photo class for stock. |
| Evolarshop | ✅ Deployed | Public Nosto GraphQL search API; excludes hoseless units. |
| Airco voor in huis | ✅ Deployed | WooCommerce product grid; `instock`/`outofstock` class. |
| Solago | ✅ Deployed | Shopify JSON-LD; pre-order text overrides InStock schema. |
| Hubo | ✅ Deployed | Shopify product sitemap + JSON-LD detail pages. |
| Vrijbuiter | ✅ Deployed | Category links + @graph JSON-LD; portable split units tracked. |

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

- Unit tests: 66 passed, including 2 new tests for Klimaatshop and Airco-Webwinkel adapters.
- Shell syntax: clean.
- `git diff --check`: clean.
- Live local dry-run on 2026-07-03 (post Klimaatshop/Airco-Webwinkel expansion): all 27 retailers ran without errors. New retailers: Klimaatshop 2/0, Airco-Webwinkel 1/0.
- GitHub Actions run `28665053635` for commit `3ce87c2`: succeeded in 3m59s. Verification execution `airco-tracker-job-3tkqxj1`: Succeeded.
- Production image: `aircotrackertdzvfmmi.azurecr.io/airco-tracker:3ce87c28a3b6e95e549415c0a5bb486d2eba7a66`.
- Prior runs retained in git history.
- Expected per-product warnings remain for one retired Obelink URL, two Kampeerwereld URLs returning HTTP 410, and one De'Longhi product missing JSON-LD offer. Their adapters still completed successfully; these warnings do not mark the retailer check as failed.

## Updating this handoff

Replace stale status instead of appending a diary. Always record the deployed commit, active retailer count, external API review state, exact verification evidence, and next concrete action. Never include email addresses, secret values, tokens, passwords, or unnecessary personal information.
