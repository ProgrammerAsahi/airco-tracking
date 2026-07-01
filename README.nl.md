# Airco Tracker NL

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
  <a href="./README.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/README-Nederlands-f58220"></a>
</p>

Een lichte voorraadtracker voor mobiele airco's in Nederland, geschikt voor lokaal gebruik en een wachtwoordloze implementatie in Azure. De tracker controleert momenteel:

- Coolblue
- MediaMarkt NL
- bol.com via de officiële Marketing Catalog API (Affiliate API-inloggegevens vereist)

Er wordt alleen een e-mail verstuurd wanneer een product voor het eerst als bestelbaar wordt gevonden of van niet leverbaar naar leverbaar verandert. Dezelfde melding wordt dus niet elke tien minuten opnieuw verstuurd. Als één winkel niet bereikbaar is, gaan de controles van de andere winkels gewoon door.

De zoekpagina van bol.com wordt niet langer gescrapet: IP-adressen van Azure-datacenters ontvangen HTTP 403 en robots.txt van bol.com beperkt dit zoekpad expliciet. Zolang de officiële API-inloggegevens niet zijn ingesteld, blijft de bol.com-adapter duidelijk uitgeschakeld en blijven Coolblue en MediaMarkt normaal werken.

## Azure-architectuur

De productieomgeving gebruikt:

```text
Container Apps Scheduled Job
  ├─ Managed Identity → Blob Storage (voorraadstatus)
  ├─ Managed Identity → Communication Services Email (meldingen)
  └─ Managed Identity → Key Vault (optionele externe inloggegevens)
```

In Azure worden geen e-mailwachtwoord, Storage key, Communication Services key of ACR-wachtwoord opgeslagen. Het e-mailadres van de ontvanger en de BTU- en prijsfilters zijn geen geheimen en worden als normale configuratie meegegeven. Key Vault is uitsluitend bedoeld voor externe inloggegevens die niet kunnen worden vermeden.

### De officiële bol.com-API inschakelen

De bol Marketing Catalog API biedt officiële productzoekresultaten, het beste Nederlandse aanbod, prijzen en bezorginformatie. Meld je eerst aan voor het bol Affiliate Programma en maak in het onderdeel Open API van het Affiliate Portal een Client ID en Client Secret aan. Plaats deze gegevens nooit in broncode, GitHub Variables of chatberichten.

Voer na de implementatie lokaal uit:

```bash
./scripts/configure-bol-api.sh
```

Het script vraagt om het Client Secret zonder dit op het scherm te tonen, slaat beide gegevens op in Azure Key Vault, stelt alleen niet-gevoelige GitHub Actions-variabelen in en start een implementatie. Tijdens de uitvoering leest de container de geheimen via Managed Identity.

## Lokaal uitvoeren

### 1. Installeren

```bash
cd ~/airco-tracking-nl
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

Bewerk `.env` en vul het e-mailadres van de ontvanger en de SMTP-instellingen in. Gmail-gebruikers moeten tweestapsverificatie inschakelen en een app-wachtwoord aanmaken; gebruik niet het normale accountwachtwoord.

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
EMAIL_TO=asahi.lee.eu@outlook.com ./scripts/deploy-azure.sh
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

### Optioneel geheimen uit Key Vault laden

Azure werkt standaard volledig zonder wachtwoorden, daarom is Key Vault aanvankelijk leeg. Als een winkel later een API-key vereist, maak dan een Key Vault secret aan en stel het volgende in:

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
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

Als `gh` niet beschikbaar of niet aangemeld is, toont het configuratiescript de volgende vijf waarden. Voeg ze handmatig toe onder **Settings → Secrets and variables → Actions → Variables**:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP
EMAIL_TO
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
