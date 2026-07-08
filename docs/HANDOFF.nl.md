# Airco Tracker — actuele overdracht

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
  <a href="./HANDOFF.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/HANDOFF-Nederlands-f58220"></a>
</p>

Laatst bijgewerkt: 2026-07-08 (Europe/Amsterdam)

Documentatieregel: werk de Chinese, Engelse en Nederlandse handoffvarianten tegelijk bij wanneer actuele status, verificatie-evidence, blockers of volgende stappen wijzigen.

## Huidig doel

Draai een betrouwbare, onderhoudsarme voorraadtracker voor mobiele airco's die naar Nederlandse en Franse adressen kunnen worden geleverd, met een landgebaseerde architectuur voor verdere Europese uitbreiding. Productie draait elke tien minuten in Azure, onderhoudt een actuele voorraad-snapshot voor het publieke dashboard en stuurt alleen e-mail bij first-seen of newly-restocked producten die door de alertfilters komen.

Het project is gemigreerd van `airco-tracking-nl` naar `airco-tracking`. Adapters zijn nu per land georganiseerd: `airco_tracker/adapters/nl/`, `airco_tracker/adapters/fr/` en gedeelde logica in `airco_tracker/adapters/shared/`. De `COUNTRIES` environment variable bepaalt actieve landen; productie gebruikt momenteel `nl,fr`.

## Repository en productie

- Repository: `https://github.com/ProgrammerAsahi/airco-tracking`
- Branch: `main`
- Local path: `~/airco-tracking`
- GitHub workflow: `Deploy to Azure`
- Azure resource group: `airco-tracker-rg`
- Container Apps job: `airco-tracker-job`
- Schedule: `*/10 * * * *` (UTC)
- Alert state: Azure Blob Storage, `airco-tracker/state.json`
- Live inventory: Azure Blob Storage, `airco-tracker/inventory.json`
- Notifications: Azure Communication Services Email
- Dashboard consumer: `https://github.com/ProgrammerAsahi/airco-tracking-web`
- Dashboard live URL: `https://airco-tracker.eu/`
- Deployment workflow: pure Markdown/docs-wijzigingen worden door `paths-ignore` genegeerd en triggeren geen productiedeployment.

Registreer geen secrets, e-mailadressen, tokens, wachtwoorden of onnodige persoonsgegevens in dit bestand.

## Actieve retailers

De applicatie registreert momenteel 45 credential-free adapters: 28 Nederlandse adapters en 17 Franse adapters.

Nederlandse adapters:

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
- Bostools

Franse adapters:

- Castorama
- Auchan
- Rue du Commerce
- Electro Dépôt France
- Costway France
- Maison Energy
- Create France
- Evolarshop France
- Klarstein France
- Trotec France
- De'Longhi France
- Lidl France
- Action France
- H2R Équipements
- Obelink France
- Narbonne Accessoires
- Mon Camping Car

Uitgestelde Franse adapters:

- Boulanger: lokale/GitHub-hosted requests kunnen de server-rendered search page lezen, maar Azure Container Apps outbound requests raken consequent een 60-second read timeout. Parser blijft in `adapters/fr/boulanger.py`; registreer pas na stabiele API of officiële/publieke alternatieve bron.
- Brico Dépôt France: lokale requests lezen category JSON-LD en Fasterize/smartcache fragment, maar Azure Container Apps krijgt te kleine/onbruikbare antwoorden. Parser blijft in `adapters/fr/bricodepot.py`; registreer pas na stabiele databron.
- Direct 403/anti-bot backlog: zie `docs/RETAILER_403_BACKLOG.md` en de taalvarianten.

## Belangrijke semantiek

- Track alleen echte compressorairco's. Sluit aircoolers, ventilatoren, ontvochtigers, raamkits, slangen, accessoires, vaste split systems en quote-only listings uit.
- `available=True` betekent nu online leverbaar naar het doelbezorgland. Alleen winkelvoorraad, alleen afhalen, verlopen deals, levertijden van meerdere weken en voorverkoop mogen geen directe-stock e-mail triggeren.
- Voorverkoopproducten mogen op het dashboard verschijnen, maar triggeren geen directe-stock alert; een overgang van voorverkoop naar directe voorraad moet wel alerten.
- Eén retailerfout mag andere retailers niet blokkeren. Een gefaalde site behoudt de laatste succesvolle voorraad met `status: error` / `stale: true`.
- Live inventory en alert state zijn gescheiden. Voorraad-snapshots passen geen prijs-, BTU- of brand-alertfilters toe; die filters gelden alleen voor e-mailalerts.
- `inventory.json` schema version `1` is een productiecontract tussen backend en frontend. Breaking changes vereisen coördinatie tussen beide repositories en een expliciete schema bump.
- Blob-container blijft privé; browsers lezen alleen via de same-origin frontend API.

## Configuratie en secret model

- `MAX_PRICE_EUR=1500`
- `MIN_BTU=7000`
- `COUNTRIES=nl,fr` in productie
- `EMAIL_LANG=zh` in productie (`zh`, `nl`, `en` ondersteund)
- Productie-recipient staat in Key Vault secret `notification-email`.
- GitHub bewaart alleen `KEY_VAULT_SECRET_MAP=EMAIL_TO=notification-email`, niet het echte adres.
- Third-party credentials moeten in Key Vault en via Managed Identity worden gelezen.
- SMTP-credentials zijn alleen lokaal in `.env`; Azure gebruikt passwordless Communication Services.

## Frontendcontract

- Backend is de enige producer van private `airco-tracker/inventory.json`.
- Frontend `airco-tracking-web` leest de Blob via same-origin `/api/inventory` en runtime Managed Identity.
- Frontend filtert per bezorgland via URL state `/deliver-to/nl`, `/deliver-to/fr`; displaytaal is onafhankelijke query/user preference state.
- `delivery_coverage` is site-level metadata. Widened coverage wordt alleen toegevoegd met officiële policy-page evidence.
- Schema- of semantiekwijzigingen moeten tegelijk backend producer/tests, frontend validator/tests, browser types/UI, fixture, README en beide handoffs bijwerken.

## AliExpress / Conrad-status

- Conrad.nl is niet geregistreerd. Gewone pagina's geven Cloudflare 403 voor projectrequests; de officiële Price & Availability API vereist allowlist/approval. Omzeil de anti-botlaag niet.
- AliExpress affiliate account is goedgekeurd, maar Open Platform/API-status moet opnieuw in de portal worden gecontroleerd voordat ontwikkeling start. Gebruik alleen officiële Affiliate/Open Platform API's en bewaar geen buyer-, order-, payment- of andere persoonsgegevens.

## Mogelijke volgende stappen

Dit zijn opties, geen automatische toestemming:

1. France 403/API backlog: zoek stabiele officiële/publieke datapaden voor Leroy Merlin, Darty, ManoMano, Fnac, Carrefour, Cdiscount, E.Leclerc, BUT, Conforama, Ubaldi, Bricomarché, Mr.Bricolage, Weldom, Qlima, Rakuten France en La Redoute.
2. Conrad API: wacht op allowlist/approval; controleer Developer Portal vóór ontwikkeling.
3. AliExpress API: controleer portalstatus opnieuw en bevestig app key/secret plus officiële signing flow vóór implementatie.

## Standaard lokale verificatie

```bash
cd ~/airco-tracking
.venv/bin/pip install --no-deps --force-reinstall .
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

Voor inventory contract-wijzigingen moet ook de frontend worden geverifieerd (zie `~/airco-tracking-web/HANDOFF.md`).

## Deploymentcommando's (alleen met toestemming)

```bash
# Backend: push to main triggers .github/workflows/deploy.yml automatically.
IMAGE_TAG=$(git -C ~/airco-tracking rev-parse --short=12 HEAD) \
  AZURE_RESOURCE_GROUP=airco-tracker-rg \
  ~/airco-tracking/scripts/deploy-application.sh

# Trigger a verification job execution:
az containerapp job start -n airco-tracker-job -g airco-tracker-rg
```

## Deze handoff bijwerken

Vervang verouderde status in plaats van een dagboek toe te voegen. Registreer altijd deployed commit, active retailer count, external API review state, frontend contract compatibility, exacte verificatie-evidence en volgende concrete actie. Neem geen e-mailadressen, secretwaarden, tokens, wachtwoorden of onnodige persoonsgegevens op.
