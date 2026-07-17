# AliExpress Affiliate API integration

[中文](ALIEXPRESS_INTEGRATION.zh.md) | **English**

## Status

Airco Tracker has a minimal signed client for the approved AliExpress Affiliate Standard API and SKU Dimension API. France and the Netherlands share the protocol, validation, discovery cache, product filtering, and SKU parsing code, but each market has a separate adapter because delivery eligibility is destination-specific.

The adapters are intentionally **not registered as production stock sources yet**. The approved `aliexpress.affiliate.product.sku.detail.get` response documents product identity, localized titles, price and tax, shipping fees, dispatch country, delivery estimates, and SKU properties. It does not document stock quantity, availability, or orderability. A returned SKU is therefore useful catalogue and logistics evidence, but it is not sufficient proof of immediate stock.

## Read-only APIs

- `aliexpress.affiliate.product.query` discovers candidate portable air conditioners at low frequency.
- `aliexpress.affiliate.product.sku.detail.get` inspects at most 20 SKUs for one product and one destination country.
- No buyer, order, payment, seller-management, or other personal-data API is used.

Every request fixes the gateway and method allowlist, uses HMAC-SHA256 signing, bounds request and response sizes, and excludes credentials and business parameters from logs. HTTP retries are limited to transient transport failures and `429`, `502`, `503`, or `504` responses.

## Availability boundary

The following fields must never be interpreted as immediate stock by themselves:

- SKU presence;
- a positive price, tax amount, or discount;
- a shipping fee or dispatch country;
- minimum, maximum, or estimated delivery days;
- a product or promotional link.

Stock-looking undocumented response keys are ignored. The shared adapter can read one exact, allowlisted field only after that field has been independently verified and explicitly configured by the adapter. With no verified field, relevant offers remain `unknown` and the site becomes stale rather than available or sold out. “No query result” has been observed both as direct code `405` and as `code=15, sub_code=405`; both are unknown, never sold out.

When `sku_ids` is omitted, the endpoint returns at most 20 SKUs. A response containing exactly 20 explicitly unavailable variants is therefore still `unknown`: it cannot prove that an orderable 21st variant does not exist. One explicitly orderable variant is sufficient to prove product availability, while more than 20 returned rows violate the documented contract and are rejected.

## Country and product rules

FR requests use `ship_to_country=FR`, `target_currency=EUR`, and `target_language=FR`; NL requests use `NL`, `EUR`, and `NL`. Results are not labelled `eu`, because an AliExpress seller may enable one destination and disable another.

Candidate and SKU filters accept compressor-based portable/mobile air conditioners and PortaSplit-style portable split systems. They reject evaporative air coolers, fans, USB/desktop mini coolers, hoses, window kits, remotes, filters, covers, spare parts, fixed wall splits, window units, and roof units. Prices must be positive EUR consumer prices in the configured safety range. Preorder wording is retained separately and can never trigger an immediate-stock alert.

## Secrets and runtime safety

Production Managed Identity hydrates:

```text
ALIEXPRESS_APP_KEY    <- Key Vault: aliexpress-app-key
ALIEXPRESS_APP_SECRET <- Key Vault: aliexpress-app-secret
```

`ALIEXPRESS_TRACKING_ID` is optional and is not stored until a real tracking ID is configured. Credentials never enter source code, images, Bicep parameters, Service Bus messages, or logs. A missing credential fails only the AliExpress adapter; scanner construction and other retailers continue normally.

Discovery is cached per destination for 12 hours, while SKU details would be refreshed on each adapter run. When the API supplies complete pagination metadata, metadata and accumulated raw row counts must remain consistent before a snapshot is cached. The production Standard API currently omits `total_page_no`, and requesting an apparently valid next page may return `405`; this form is therefore treated only as an explicitly truncated, one-page diagnostic window. It cannot activate a future verified stock field. Page counts, candidate counts, streamed response sizes, and per-call timeouts are bounded, and a conservative per-country budget is checked before every call. Invalid cache content, mismatched product IDs or item URLs, unexpected URL hosts, non-EUR prices, malformed payloads, or budget exhaustion fail closed. A fixed canonical URL derived from the validated product ID is used as inventory identity so locale-host and tracking-query changes cannot manufacture stock transitions.

## Production contract probe (17 July 2026)

The production app credentials were validated against the official gateway without logging the secret, signature, or raw request. The sanitized probe established the following:

- Standard product-query responses omit `total_page_no`; advertised totals are advisory and the next page can return `405`.
- The live SKU array is wrapped as `ae_item_sku_info.traffic_sku_info_list`; the documented direct-array form remains supported.
- One example product returned one deliverable SKU for France but `code=15, sub_code=405` for the Netherlands, confirming that delivery evidence must remain country-bound.
- The returned SKU keys were limited to identity, price/tax/discount, shipping fee, dispatch country, delivery-window, image, EAN, and properties. No stock quantity, availability, or orderability key was present.
- AliExpress search is fuzzy: `Midea PortaSplit` returned catalogue rows but none passed the strict portable-air-conditioner filter in the sampled page.
- Repeated research queries reached `ApiCallLimit`. Candidate discovery must therefore stay low-frequency and cached; it must not run every ten-minute scanner cycle.

These observations are covered by regression tests. They do not satisfy the stock-evidence requirement, so the adapters remain unregistered.

## Production-enablement checklist

1. ~~Run read-only FR and NL queries with the production app.~~ Completed on 17 July 2026.
2. ~~Inspect actual SKU response keys without logging values that may be sensitive.~~ Completed; no stock field was present.
3. Correlate a sample of returned and absent SKUs against the destination-specific checkout page, including sold-out and preorder cases.
4. Obtain official documentation or repeatable evidence for one unambiguous SKU orderability/inventory field.
5. Configure only that field, add regression fixtures for true, false, missing, malformed, and contradictory cases, and keep all other stock-looking keys ignored.
6. Register the FR and NL adapters with delivery coverage `{fr}` and `{nl}` respectively.
7. Deploy with first-seen alerts disabled, establish the production baseline, verify no false outbox events, then restore normal alerting.

If step 4 cannot be satisfied, the client and inspection adapters remain available for research but AliExpress must not appear on the live inventory page.
