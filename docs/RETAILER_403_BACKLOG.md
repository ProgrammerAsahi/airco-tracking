# Retailer 403 / anti-bot backlog

<p align="center">
  <a href="./RETAILER_403_BACKLOG.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/docs-简体中文-d73a49"></a>
  <a href="./RETAILER_403_BACKLOG.md"><img alt="English" src="https://img.shields.io/badge/docs-English-0969da"></a>
</p>

Last updated: 2026-07-14

This document tracks country-specific retailer sites that are worth revisiting
but are not currently enabled because normal tracker requests hit direct `403`,
captcha, or similar anti-bot blocking. Keep these separate from normal parser
backlog items: do not register an adapter until a stable, public,
production-safe data source has been found and verified from Azure Container
Apps.

When this file changes, update the Chinese and English variants together.

## France

E.Leclerc has been removed from this backlog: its anti-bot storefront pages are
not scraped, but the first-party same-origin frontend API now provides a stable
first-party data path for product discovery and live stock details. The adapter fails
closed when that API is malformed, contradictory, or unavailable.

### Strict direct-403 sites

These were explicitly observed or recorded as direct `403` blockers during the
France expansion work.

| Site | Entry URL | Type | Why it matters | Next exploration path |
| --- | --- | --- | --- | --- |
| Leroy Merlin | <https://www.leroymerlin.fr/recherche/climatiseur%20mobile> | DIY / home improvement | One of the highest-value French home-improvement retailers. | Look for page-internal product/search APIs, sitemap category URLs, or official/public product feeds. |
| Darty | <https://www.darty.com/nav/recherche/climatiseur%20mobile.html> | Electronics / appliance chain | Major appliance retailer; same ecosystem as Fnac. | Inspect browser network calls and any structured search endpoints; verify direct product pages separately. |
| ManoMano | <https://www.manomano.fr/q/climatiseur-mobile> | DIY marketplace | Very relevant category coverage, especially portable AC and HVAC accessories. | Search for public search APIs or marketplace feed endpoints; filtering must be strict because accessories are common. |
| Fnac | <https://www.fnac.com/SearchResult/ResultList.aspx?Search=climatiseur%20mobile> | General marketplace / Fnac-Darty group | Could add incremental marketplace coverage beyond Darty. | Investigate shared Fnac/Darty APIs; watch for third-party marketplace stock and presale ambiguity. |
| Carrefour | <https://www.carrefour.fr/s?q=climatiseur%20mobile> | Hypermarket / marketplace | Large French retail footprint; heatwave seasonal stock is plausible. | Look for search JSON endpoints, product feeds, or store-independent online availability data. |
| Ubaldi | <https://www.ubaldi.com/recherche/climatiseur-mobile.php> | Appliance e-commerce | Strong French appliance specialist. | Explore category/product structured data and any public search endpoint exposed to the browser. |
| Bricomarché | <https://www.bricomarche.com/recherche?text=climatiseur%20mobile> | DIY / home improvement | Regional store network; useful long-tail coverage. | Find true category/search endpoints and separate online delivery from store-only availability. |
| Mr.Bricolage | <https://www.mr-bricolage.fr/recherche?search_query=climatiseur%20mobile> | DIY / home improvement | Long-tail DIY retailer with seasonal portable AC stock. | Investigate product/category APIs and whether stock is local-store-only. |
| Qlima France | <https://www.qlima.fr/climatiseur-mobile/> | Brand / HVAC | Qlima is highly relevant in portable AC. | Look for official catalog/product feeds or alternate storefront URLs with reliable stock semantics. |

### 403 / captcha / anti-bot equivalent blockers

These are treated as blocked for tracker purposes even when the first symptom
was not always a plain direct `403`. They require the same level of caution:
find a stable public data path first, then verify from Azure before enabling.

| Site | Entry URL | Type | Observed blocker | Next exploration path |
| --- | --- | --- | --- | --- |
| BUT | <https://www.but.fr/recherche?text=climatiseur%20mobile> | Furniture / appliance chain | Grouped with 403/captcha/anti-bot blocked requests during implementation. | Inspect browser-side search API and product detail JSON; filter coolers/fans aggressively. |
| Conforama | <https://www.conforama.fr/recherche?search=climatiseur%20mobile> | Furniture / appliance chain | Grouped with 403/captcha/anti-bot blocked requests during implementation. | Look for category APIs and online-delivery stock fields; products may mix ACs and air coolers. |
| Weldom | <https://www.weldom.fr/> | DIY / home improvement | Page reachable in survey, but search routing/data was not stable and later grouped with blocked backlog. | Locate the real search/category URL first, then verify server-rendered or public API product data. |
| Rakuten France | <https://fr.shopping.rakuten.com/s/climatiseur+mobile> | Marketplace | Grouped with 403/captcha/anti-bot blocked requests; high false-positive risk from third-party listings. | Only consider if a stable public listing API exposes clear condition, seller, stock, and presale fields. |
| La Redoute | <https://www.laredoute.fr/search.aspx?searchkeyword=climatiseur%20mobile> | Marketplace / home | Grouped with 403/captcha/anti-bot blocked requests; likely third-party mixed listings. | Look for public search data and strict marketplace filtering; avoid ambiguous seller/backorder stock. |
| Euro Accessoires | <https://www.euro-accessoires.fr/recherche?controller=search&s=climatiseur> | Camper / accessories | Normal requests receive a tiny JavaScript/AES challenge page instead of usable product data. | Look for an official/public feed or sitemap path that does not require solving the JS challenge. |
| Manutan France | <https://www.manutan.fr/fr/maf/search?text=climatiseur%20mobile> | B2B / industrial | Normal requests are redirected to a Radware Bot Manager captcha page. | Do not bypass the captcha; only revisit if a public API/feed is documented or exposed without anti-bot gating. |

### Adjacent French backlog that is not counted as 403

These should stay out of the 403 list so future work can pick the right
strategy.

| Site | Current status | Notes |
| --- | --- | --- |
| Boulanger | Azure production read timeout | Local/GitHub-hosted requests can read the page, but Azure Container Apps consistently hits a 60-second read timeout. |
| Brico Dépôt France | Azure production receives too-small/unusable responses | Parser code and tests are retained, but both the normal category page and smartcache fragment are unstable from Azure. |
| Cdiscount | JS shell / anti-bot / no stable server-side product data yet | Worth revisiting, but not recorded as a plain direct-403 site. |
| Habitat et Jardin | No stable product cards on tested search page | Needs a better category/search data source rather than anti-bot work. |
| Olimpia Splendid France | Brand/catalog source without reliable direct stock | Useful for product reference, not currently a stock-alert source. |
| Midea France | Brand/catalog source without reliable direct stock | Useful for product reference, not currently a stock-alert source. |
| Climshop | Fixed/window/split-heavy tested entries | Do not enable until mobile/portable stock can be separated reliably. |
| Clim Planete | Fixed/window/split-heavy tested entries | Do not enable until mobile/portable stock can be separated reliably. |
| Alternate France | Search/sitemap currently yields EcoFlow WAVE accessories, not air conditioners | Low value until real portable AC products appear in the public sitemap/search results. |
| Maxiburo | B2B Nuxt/SPA search without stable server-rendered product data | Low priority; revisit only if a public product API with stock semantics is identified. |
| Bruneau | B2B Nuxt/SPA search without stable server-rendered product data | Same platform family as Maxiburo; low priority for consumer heatwave alerts. |
| Seton | Search returned safety/signage/mobile-industrial false positives rather than air conditioners | Not anti-bot blocked for the tested catalogsearch path, but not relevant enough to track. |
| Airton | Fixed split/monobloc installation-heavy catalog | Useful as a brand/catalog reference, not aligned with the current mobile/portable stock model. |
| Espace Aubade | Search paths returned 404/500-style unstable pages | HVAC/showroom channel; revisit only with a stable category/API and clear direct-stock semantics. |

## Revisit checklist

Before moving any site from this backlog into `ADAPTERS`:

1. Prefer official/public APIs, product feeds, sitemaps, or server-rendered data.
2. Do not bypass captcha or anti-bot controls.
3. Verify the source from both local development and Azure Container Apps; local-only success is not enough.
4. Prove that immediate stock, presale/backorder, store-only stock, and unavailable states are distinct.
5. Filter out air coolers, fans, humidifiers, accessories, fixed split systems, and quote-only listings.
6. Add parser tests for stock, presale, false-positive filtering, and markup/API drift.
7. Run a production job after deployment and require `stale_site_count == 0` before considering the site active.
