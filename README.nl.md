# Airco Tracker NL

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
  <a href="./README.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/README-Nederlands-f58220"></a>
</p>

Een lichte voorraadtracker voor mobiele airco's in Nederland, geschikt voor lokaal gebruik en een wachtwoordloze implementatie in Azure. De tracker controleert momenteel:

- Coolblue
- MediaMarkt NL
- EP.nl
- Electro World
- Wehkamp
- Lidl Nederland
- GAMMA
- KARWEI
- Praxis
- Alternate.nl
- Trotec
- Klarstein
- FlinQ
- Action Webshop
- Expert.nl
- De'Longhi Nederland
- Obelink
- Kampeerwereld
- Create Nederland
- Costway NL
- Evolarshop
- Airco voor in huis
- Solago
- Hubo
- Vrijbuiter
- Klimaatshop
- Airco-Webwinkel

Er wordt alleen een e-mail verstuurd wanneer een product voor het eerst als bestelbaar wordt gevonden of van niet leverbaar naar leverbaar verandert. Dezelfde melding wordt dus niet elke tien minuten opnieuw verstuurd. Als één winkel niet bereikbaar is, gaan de controles van de andere winkels gewoon door.

De voorraad van EP.nl wordt gelezen uit server-side weergegeven productkaarten. Electro World wordt gelezen via de openbare, alleen-lezen productzoekindex die de webwinkel zelf gebruikt; de openbare zoekconfiguratie wordt bij elke uitvoering dynamisch opgehaald. Wehkamp wordt gelezen uit de primaire productgegevens op de categoriepagina. Geen van deze drie integraties vereist een account of geheime inloggegevens. Wehkamp verwijdert uitverkochte producten uit de categorie, waardoor een expliciet lege categorie een geldige status is; zodra een product na aanvulling terugkeert, volgt direct een melding voor nieuw gevonden voorraad.

De door robots.txt beperkte zoekroute van Lidl wordt niet gescrapet. Producten worden ontdekt via Lidl's openbare productsitemap, waarna de JSON-LD-voorraad van elke echte mobiele airco wordt gelezen. GAMMA en KARWEI delen een parser voor server-side weergegeven productkaarten; alleen `ONLINE_AVAILABLE` telt als bezorgbaar, waardoor winkelvoorraad en alleen-afhalen geen melding veroorzaken. Praxis controleert zowel de actuele beschikbaarheid als de bezorgmethoden, meldt alleen producten die op een Nederlands adres kunnen worden bezorgd en sluit split-airco's, aircoolers en accessoires uit.

Alternate.nl, FlinQ en Action Webshop ontdekken nieuwe modellen via de productsitemaps die in hun robots.txt-bestanden zijn gepubliceerd en lezen daarna de voorraad op de productpagina. Action blijft ook bekende verlopen seizoensdeals controleren, zodat een opnieuw geactiveerde URL direct wordt gevonden. Trotec en Klarstein worden gelezen uit server-side weergegeven categoriegegevens. Een levertijd van meerdere weken, voorverkoop of een product dat alleen bestelbaar is, telt bij Trotec niet als directe voorraad: alleen expliciet `Op voorraad` activeert een melding. Klarstein moet een expliciete online voorraadstatus tonen. Alle vijf adapters sluiten aircoolers, ventilatoren en accessoires uit.

Expert telt uitsluitend producten die werkelijk online kunnen worden besteld; alleen winkelvoorraad veroorzaakt nooit een melding. De'Longhi leest de officiële JSON-LD op iedere productpagina en behandelt `Breng mij op de hoogte` als niet leverbaar. Obelink en Kampeerwereld blijven bekende seizoensproducten controleren, ook wanneer die tijdelijk uit categoriepagina's verdwijnen. Create behandelt zowel `Presale` als `Verzending vanaf` als niet direct leverbaar.

Costway NL leest de Magento-categoriepagina en gebruikt de `qty-N`-voorraadaanduiding; Evolarshop bevraagt de openbare Nosto-zoek-API en sluit producten zonder afvoerslang ("zonder afvoerslang") uit als niet-compressorunit; Airco voor in huis gebruikt de WooCommerce-status `instock`/`outofstock`; Solago leest Shopify JSON-LD, waarbij `Voorbestelling` en `Levering vanaf` de InStock-markering overschrijven als niet leverbaar. Hubo heeft geen airco-categoriepagina en ontdekt mobiele airco's via de Shopify-productsitemap; Vrijbuiter volgt draagbare split-units voor caravan en camper (zoals Mestic SPA, Qlima MS-AC) en sluit aircoolers en accessoires uit. Klimaatshop is een gespecialiseerde aircohandelaar waar product-URL's uit het `data-url`-attribuut worden gelezen en de voorraad uit de `.stock`-span; Airco-Webwinkel is een WooCommerce-winkel die via de productsitemap wordt ontdekt, met JSON-LD-detailpagina's.

Conrad.nl is nog niet ingeschakeld: gewone verzoeken vanuit zowel Azure als lokale uitvoering ontvangen Cloudflare HTTP 403. Conrad biedt via het Developer Portal een officiële Price & Availability API, maar toegang moet afzonderlijk worden aangevraagd. Dit project omzeilt geen anti-botbeveiliging.

## Azure-architectuur

De productieomgeving gebruikt:

```text
Container Apps Scheduled Job
  ├─ Managed Identity → Blob Storage (voorraadstatus)
  ├─ Managed Identity → Communication Services Email (meldingen)
  └─ Managed Identity → Key Vault (ontvanger en optionele externe inloggegevens)
```

In Azure worden geen e-mailwachtwoord, Storage key, Communication Services key of ACR-wachtwoord opgeslagen. Het e-mailadres van de ontvanger staat als het geheim `notification-email` in Key Vault; GitHub bewaart alleen de koppeling `EMAIL_TO=notification-email`. Prijs- en BTU-limieten blijven gewone omgevingsconfiguratie.

## Lokaal uitvoeren

### 1. Installeren

```bash
cd ~/airco-tracking-nl
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

Bewerk `.env` en vul het e-mailadres van de ontvanger en de SMTP-instellingen in. Gmail-gebruikers moeten tweestapsverificatie inschakelen en een app-wachtwoord aanmaken; gebruik niet het normale accountwachtwoord.

Optioneel: stel `EMAIL_LANG` in (standaard `zh`): `zh` voor Chinees, `nl` voor Nederlands, `en` voor Engels.

Voer de opdrachten vanuit de projectmap uit. Als dat niet mogelijk is, stel dan `AIRCO_TRACKER_HOME=~/airco-tracking-nl` in.

### 2. Controleren

Controleer de pagina-analyse zonder e-mail te versturen of de status bij te werken:

```bash
.venv/bin/airco-tracker check --dry-run --show-all
```

Verstuur een testmail:

```bash
.venv/bin/airco-tracker send-test
```

Controleer de backendconfiguratie en toegang tot de statusopslag zonder e-mail te versturen:

```bash
.venv/bin/airco-tracker doctor
```

Voer ten slotte één echte controle uit:

```bash
.venv/bin/airco-tracker check
```

De eerste echte uitvoering meldt standaard producten die al op voorraad zijn. Daarna worden alleen nieuw beschikbare producten gemeld. Stel `ALERT_ON_FIRST_SEEN=false` in `.env` in om de eerste melding over te slaan.

### 3. Op de achtergrond uitvoeren in macOS

```bash
./install-launch-agent.sh
```

De macOS LaunchAgent controleert elke tien minuten en wordt na het inloggen automatisch hervat. Bekijk de logboeken met:

```bash
tail -f ~/airco-tracking-nl/tracker.log ~/airco-tracking-nl/tracker.err.log
```

Stop de achtergrondtaak met:

```bash
./uninstall-launch-agent.sh
```

## Implementeren in Azure

Vereisten:

- Een actief Azure-abonnement.
- Azure CLI, met `az login` uitgevoerd.
- Rechten om resourcegroepen, roltoewijzingen en de benodigde Azure-resources aan te maken.

Implementeren:

```bash
cd ~/airco-tracking-nl
EMAIL_TO=you@example.com ./scripts/deploy-azure.sh
```

Het script:

1. Maakt ACR, Blob Storage, Key Vault, een Container Apps Environment, Managed Identity en Communication Services Email aan.
2. Bouwt de containerimage op afstand in ACR; lokale Docker is niet nodig.
3. Maakt een Container Apps Job die elke tien minuten wordt uitgevoerd.
4. Start direct één uitvoering om het ophalen en afleveren van e-mail te controleren.

Nieuwe Azure RBAC-rollen hebben soms enkele minuten nodig om actief te worden. Als de eerste uitvoering een 403 van ACR, Blob of Communication Services ontvangt, wacht dan kort en start opnieuw:

```bash
az containerapp job start --name airco-tracker-job --resource-group airco-tracker-nl-rg
```

Uitvoeringen en logboeken bekijken:

```bash
az containerapp job execution list \
  --name airco-tracker-job \
  --resource-group airco-tracker-nl-rg \
  --output table

az containerapp job logs show \
  --name airco-tracker-job \
  --resource-group airco-tracker-nl-rg \
  --follow
```

Het schema gebruikt UTC. `*/10 * * * *` wordt elke tien minuten uitgevoerd en wordt niet beïnvloed door zomer- of wintertijd.

### De container lokaal bouwen (optioneel)

Als Docker is geïnstalleerd:

```bash
./scripts/test-container.sh
```

`.dockerignore` sluit `.env`, status- en logbestanden expliciet uit, zodat lokale wachtwoorden niet in de image terechtkomen.

### Geheimen uit Key Vault laden

Voer dit uit om het productieadres te wijzigen zonder het te committen of in GitHub op te slaan:

```bash
./scripts/configure-notification-email.sh
```

Het script leest tijdens de migratie de bestaande GitHub-waarde of vraagt er zonder echo om, bewaart deze als `notification-email` in Key Vault en verwijdert de oude GitHub-waarde. Als een winkel later een API-key vereist, maak dan een extra Key Vault secret en breid de koppeling uit:

```text
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net
KEY_VAULT_SECRET_MAP=PARTNER_API_KEY=partner-api-key
```

De applicatie leest het geheim via Managed Identity. Het geheim komt niet in de broncode, containerimage of Bicep-parameters terecht.

## GitHub Actions CI/CD

De repository `ProgrammerAsahi/airco-tracking-nl` heeft twee workflows:

- `.github/workflows/ci.yml`: valideert Python, shellscripts en Bicep bij pull requests.
- `.github/workflows/deploy.yml`: bouwt na geslaagde tests bij een push naar `main` een onveranderlijke image met de commit-SHA als tag en werkt de Azure Job bij.

Azure-aanmelding gebruikt een kortlevend GitHub OIDC-token en geen Client Secret. De federatieve identiteit vertrouwt alleen de `main`-branch van deze repository en heeft uitsluitend Contributor-rechten op de doelresourcegroep. Deze identiteit kan geen roltoewijzingen maken en geen applicatiegeheimen uit Key Vault lezen.

### Volgorde voor de eerste configuratie

Maak de Azure-basis en OIDC-vertrouwensrelatie lokaal aan vóór de eerste push naar `main`, zodat de workflow niet start voordat de variabelen bestaan:

```bash
brew install azure-cli gh
az login
gh auth login

cd ~/airco-tracking-nl
EMAIL_TO=you@example.com ./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

Als `gh` niet beschikbaar of niet aangemeld is, toont het configuratiescript de volgende zes waarden. Voeg ze handmatig toe onder **Settings → Secrets and variables → Actions → Variables**:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP
EMAIL_LANG
KEY_VAULT_SECRET_MAP
```

Dit zijn identificaties of normale configuratiewaarden, geen wachtwoorden. Maak of upload geen `AZURE_CREDENTIALS`, Client Secret of toegangstoken voor het abonnement.

### Eerste push

Voor een lege GitHub-repository:

```bash
cd ~/airco-tracking-nl
git init -b main
git remote add origin https://github.com/ProgrammerAsahi/airco-tracking-nl.git
git add .
git commit -m "Initial airco tracker with Azure CI/CD"
git push -u origin main
```

`.env`, `.venv`, status- en logbestanden worden door Git genegeerd. Elke latere merge of push naar `main` voert één implementatie uit. Images gebruiken de volledige Git commit-SHA en overschrijven nooit `latest`.

## Filters

Stel filters in via `.env`:

- `MAX_PRICE_EUR=1500`: meld alleen producten van maximaal € 1.500. Producten waarvan de prijs tijdelijk onbekend is, blijven meetellen om gemiste meldingen te voorkomen.
- `MIN_BTU=7000`: meld geen producten onder 7.000 BTU. Echte airco's waarvan de BTU-waarde niet op de overzichtspagina staat, worden behouden om gemiste meldingen te voorkomen.

## Onderhoud en winkels toevoegen

Elke winkel heeft een eigen adapter onder `airco_tracker/adapters/`. Voeg een winkel toe door een adapter te implementeren en deze in `cli.py` te registreren. Als de structuur van een webpagina verandert en er geen producten kunnen worden verwerkt, meldt de applicatie `parser found no products` in plaats van stilzwijgend te doen alsof alles uitverkocht is.

Houd een controle-interval van ten minste tien minuten aan. De productpagina blijft uiteindelijk bepalend voor voorraad, prijs en bezorging.
