# Retailer 403 / anti-bot backlog

<p align="center">
  <a href="./RETAILER_403_BACKLOG.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/docs-简体中文-d73a49"></a>
  <a href="./RETAILER_403_BACKLOG.md"><img alt="English" src="https://img.shields.io/badge/docs-English-0969da"></a>
  <a href="./RETAILER_403_BACKLOG.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/docs-Nederlands-f58220"></a>
</p>

Laatst bijgewerkt: 2026-07-06

Dit document volgt landspecifieke retailersites die de moeite waard zijn om later opnieuw te onderzoeken, maar momenteel niet zijn ingeschakeld omdat normale trackerrequests directe `403`, captcha of vergelijkbare anti-botblokkades raken. Houd deze apart van gewone parser-backlogitems: registreer geen adapter voordat een stabiele, publieke, productie-veilige databron is gevonden en vanuit Azure Container Apps is geverifieerd.

Werk bij wijzigingen de Chinese, Engelse en Nederlandse varianten tegelijk bij.

## Frankrijk

### Strikte direct-403-sites

Deze sites zijn tijdens het Frankrijk-uitbreidingswerk expliciet als direct `403` blocker waargenomen of geregistreerd.

| Site | Entry URL | Type | Waarom belangrijk | Volgende onderzoekspad |
| --- | --- | --- | --- | --- |
| Leroy Merlin | <https://www.leroymerlin.fr/recherche/climatiseur%20mobile> | DIY / home improvement | Een van de waardevolste Franse home-improvement retailers. | Zoek naar pagina-interne product/search API's, sitemap category URLs of officiële/publieke productfeeds. |
| Darty | <https://www.darty.com/nav/recherche/climatiseur%20mobile.html> | Electronics / appliance chain | Grote appliance retailer; zelfde ecosysteem als Fnac. | Inspecteer browser network calls en gestructureerde search endpoints; verifieer productpagina's apart. |
| ManoMano | <https://www.manomano.fr/q/climatiseur-mobile> | DIY marketplace | Zeer relevante category coverage, vooral portable AC en HVAC-accessoires. | Zoek naar publieke search API's of marketplace feed endpoints; filtering moet strikt zijn omdat accessoires vaak voorkomen. |
| Fnac | <https://www.fnac.com/SearchResult/ResultList.aspx?Search=climatiseur%20mobile> | General marketplace / Fnac-Darty group | Kan extra marketplace coverage boven Darty bieden. | Onderzoek gedeelde Fnac/Darty API's; let op third-party marketplace stock en presale-ambiguïteit. |
| Carrefour | <https://www.carrefour.fr/s?q=climatiseur%20mobile> | Hypermarket / marketplace | Grote Franse retaildekking; hittegolfseizoensvoorraad is plausibel. | Zoek naar search JSON endpoints, productfeeds of online availability data onafhankelijk van winkels. |
| Ubaldi | <https://www.ubaldi.com/recherche/climatiseur-mobile.php> | Appliance e-commerce | Sterke Franse appliance specialist. | Verken category/product structured data en publieke search endpoints die de browser blootstelt. |
| Bricomarché | <https://www.bricomarche.com/recherche?text=climatiseur%20mobile> | DIY / home improvement | Regionaal winkelnetwerk; waardevolle long-tail dekking. | Vind echte category/search endpoints en scheid online delivery van store-only availability. |
| Mr.Bricolage | <https://www.mr-bricolage.fr/recherche?search_query=climatiseur%20mobile> | DIY / home improvement | Long-tail DIY retailer met mogelijke seizoensvoorraad mobiele airco's. | Onderzoek product/category API's en of voorraad alleen local-store is. |
| Qlima France | <https://www.qlima.fr/climatiseur-mobile/> | Brand / HVAC | Qlima is zeer relevant voor portable AC. | Zoek naar officiële catalog/productfeeds of alternatieve storefront URLs met betrouwbare stocksemantiek. |

### 403 / captcha / anti-bot-equivalente blockers

Deze worden voor trackerdoeleinden als blocked behandeld, ook als het eerste symptoom niet altijd een gewone direct `403` was. Dezelfde voorzichtigheid geldt: vind eerst een stabiel publiek datapad en verifieer vanuit Azure voordat je inschakelt.

| Site | Entry URL | Type | Geobserveerde blocker | Volgende onderzoekspad |
| --- | --- | --- | --- | --- |
| BUT | <https://www.but.fr/recherche?text=climatiseur%20mobile> | Furniture / appliance chain | Tijdens implementatie gegroepeerd met 403/captcha/anti-bot blocked requests. | Inspecteer browser-side search API en product detail JSON; filter koelers/ventilatoren agressief. |
| Conforama | <https://www.conforama.fr/recherche?search=climatiseur%20mobile> | Furniture / appliance chain | Tijdens implementatie gegroepeerd met 403/captcha/anti-bot blocked requests. | Zoek category API's en online-delivery stockvelden; producten kunnen AC's en aircoolers mengen. |
| Weldom | <https://www.weldom.fr/> | DIY / home improvement | Pagina was bereikbaar in survey, maar search routing/data was niet stabiel en later gegroepeerd met blocked backlog. | Lokaliseer eerst de echte search/category URL en verifieer dan server-rendered of publieke API-productdata. |
| Rakuten France | <https://fr.shopping.rakuten.com/s/climatiseur+mobile> | Marketplace | Gegroepeerd met 403/captcha/anti-bot blocked requests; hoog false-positive risico door third-party listings. | Alleen overwegen als een stabiele publieke listing API duidelijke condition, seller, stock en presale fields toont. |
| La Redoute | <https://www.laredoute.fr/search.aspx?searchkeyword=climatiseur%20mobile> | Marketplace / home | Gegroepeerd met 403/captcha/anti-bot blocked requests; waarschijnlijk gemengde third-party listings. | Zoek publieke search data en strikte marketplace filtering; vermijd ambigue seller/backorder stock. |
| Euro Accessoires | <https://www.euro-accessoires.fr/recherche?controller=search&s=climatiseur> | Camper / accessories | Normale requests krijgen een kleine JavaScript/AES challenge page in plaats van bruikbare productdata. | Zoek naar een officiële/publieke feed of sitemap path dat geen JS challenge vereist. |
| Manutan France | <https://www.manutan.fr/fr/maf/search?text=climatiseur%20mobile> | B2B / industrial | Normale requests worden omgeleid naar een Radware Bot Manager captcha page. | Omzeil de captcha niet; alleen herbezoeken als een publieke API/feed gedocumenteerd of zonder anti-bot gating beschikbaar is. |

### Aangrenzende Franse backlog die niet als 403 telt

Deze horen buiten de 403-lijst te blijven zodat toekomstig werk de juiste strategie kan kiezen.

| Site | Current status | Notes |
| --- | --- | --- |
| Boulanger | Azure production read timeout | Lokale/GitHub-hosted requests kunnen de pagina lezen, maar Azure Container Apps raakt consequent een 60-second read timeout. |
| Brico Dépôt France | Azure production receives too-small/unusable responses | Parsercode en tests blijven staan, maar zowel de normale category page als smartcache fragment zijn vanuit Azure instabiel. |
| Cdiscount | JS shell / anti-bot / nog geen stabiele server-side productdata | Waard om opnieuw te bekijken, maar niet als gewone direct-403 geregistreerd. |
| E.Leclerc | SPA / anti-bot / nog geen stabiele server-side productdata | Waard om opnieuw te bekijken, maar niet als gewone direct-403 geregistreerd. |
| Habitat et Jardin | Geen stabiele product cards op geteste search page | Vereist een betere category/search databron in plaats van anti-botwerk. |
| Olimpia Splendid France | Brand/catalog source without reliable direct stock | Bruikbaar als productreferentie, niet als huidige stock-alert source. |
| Midea France | Brand/catalog source without reliable direct stock | Bruikbaar als productreferentie, niet als huidige stock-alert source. |
| Climshop | Fixed/window/split-heavy tested entries | Niet inschakelen totdat mobile/portable stock betrouwbaar kan worden gescheiden. |
| Clim Planete | Fixed/window/split-heavy tested entries | Niet inschakelen totdat mobile/portable stock betrouwbaar kan worden gescheiden. |
| Alternate France | Search/sitemap levert momenteel EcoFlow WAVE-accessoires op, geen airco's | Lage waarde totdat echte portable AC-producten in sitemap/search verschijnen. |
| Maxiburo | B2B Nuxt/SPA search zonder stabiele server-rendered productdata | Lage prioriteit; alleen herbezoeken bij een publieke product API met stocksemantiek. |
| Bruneau | B2B Nuxt/SPA search zonder stabiele server-rendered productdata | Zelfde platformfamilie als Maxiburo; lage prioriteit voor consumenten-hittegolfalerts. |
| Seton | Search gaf safety/signage/mobile-industrial false positives in plaats van airco's | Niet anti-bot blocked voor het geteste catalogsearch path, maar onvoldoende relevant. |
| Airton | Fixed split/monobloc installation-heavy catalog | Bruikbaar als merk/catalogusreferentie, niet passend bij het huidige mobile/portable stock model. |
| Espace Aubade | Search paths gaven 404/500-achtige instabiele pagina's | HVAC/showroomkanaal; alleen herbezoeken met stabiele category/API en duidelijke direct-stocksemantiek. |

## Checklist voor opnieuw bekijken

Voordat een site uit deze backlog naar `ADAPTERS` gaat:

1. Geef voorkeur aan officiële/publieke API's, productfeeds, sitemaps of server-rendered data.
2. Omzeil geen captcha of anti-botcontroles.
3. Verifieer de bron vanuit zowel lokale ontwikkeling als Azure Container Apps; lokaal succes alleen is niet genoeg.
4. Bewijs dat immediate stock, presale/backorder, store-only stock en unavailable states onderscheidbaar zijn.
5. Filter aircoolers, ventilatoren, luchtbevochtigers, accessoires, fixed split systems en quote-only listings uit.
6. Voeg parser tests toe voor stock, presale, false-positive filtering en markup/API drift.
7. Draai na deployment een productietaak en vereis `stale_site_count == 0` voordat de site als actief wordt beschouwd.
