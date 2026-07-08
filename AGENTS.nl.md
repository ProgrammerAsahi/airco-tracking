# Airco Tracker — gedeelde agentinstructies

<p align="center">
  <a href="./AGENTS.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/AGENTS-简体中文-d73a49"></a>
  <a href="./AGENTS.md"><img alt="English" src="https://img.shields.io/badge/AGENTS-English-0969da"></a>
  <a href="./AGENTS.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/AGENTS-Nederlands-f58220"></a>
</p>

## Missie

Onderhoud een betrouwbare, goedkope voorraadtracker voor echte mobiele compressorairco's die naar het doeladres kunnen worden geleverd. Stuur alleen een melding wanneer een product voor het eerst beschikbaar is of van niet beschikbaar naar beschikbaar verandert, en publiceer een betrouwbare private voorraad-snapshot voor het publieke read-only dashboard.

## Eerst lezen

1. Lees `docs/HANDOFF.md` voor de huidige status en volgende taak.
2. Lees de relevante adapter-, test- en infrastructuurbestanden voordat je wijzigt.
3. Gebruik de taalspecifieke README alleen voor gebruikersgerichte setupdetails (`README.md`, `README.en.md`, `README.nl.md`). Houd alle drie synchroon bij gedragswijzigingen.
4. Als inventory schema of semantiek betrokken is, inspecteer dan `~/airco-tracking-web/server/inventory.ts`, `src/types.ts`, de fixture/tests en de frontend-handoff vóór wijziging.
5. Alle Markdown-documentatie moet in Chinees, Engels en Nederlands worden onderhouden. Werk bij elke documentwijziging alle taalvarianten in dezelfde change bij.

## Niet-onderhandelbare regels

- Commit of print nooit API keys, client secrets, wachtwoorden, access tokens, SMTP-credentials of Key Vault secret values.
- Third-party credentials horen in Azure Key Vault en worden via Managed Identity gelezen.
- Geef voorkeur aan officiële API's. Gebruik anders publieke server-rendered pagina's of robots-geadverteerde sitemaps. Respecteer robots.txt en voorwaarden; omzeil nooit CAPTCHA, 403-bescherming, loginbarrières of anti-botcontroles.
- Track echte compressorairco's. Sluit aircoolers, verdampingskoelers, ventilatoren, slangen, raamkits, afstandsbedieningen, filters en andere accessoires uit.
- `available=True` betekent op dit moment bestelbaar voor levering aan het doeladres. Alleen winkelvoorraad, alleen afhalen, verlopen deals, voorverkoop en levertijden van meerdere weken mogen geen alerts triggeren.
- Een retailerfout mag de overige retailers niet stoppen. Zet een mislukte check niet om in een out-of-stock transition.
- Tests en dry-runs mogen geen e-mail sturen of productiestatus bijwerken.
- Houd alert state en live inventory gescheiden. Alertfilters mogen beschikbare producten binnen scope niet uit `inventory.json` verwijderen.
- `inventory.json` schema version `1` wordt in productie door `airco-tracking-web` geconsumeerd. Wijzig velden, betekenissen of failure/staleness-gedrag niet stilzwijgend zonder gecoördineerde cross-repository change en tests.
- Houd de `airco-tracker` Blob-container privé. Stel geen Storage credentials of SAS URL bloot aan browsercode; de same-origin Node API van de frontend leest met Managed Identity.
- Lees geen order-, koper-, betaal- of andere persoonsgegevens wanneer product-catalog en affiliate-offer scopes voldoende zijn.
- Bewaar ongerelateerde gebruikerswijzigingen in een dirty worktree.

## Architectuur

- Python package: `airco_tracker/`
- Landonafhankelijke parsing helpers: `airco_tracker/adapters/base.py` (`Adapter` ABC, prijs/BTU/presale parsing), `schema.py` (JSON-LD), `sitemap.py`
- Landgebaseerde adapter registry: `airco_tracker/adapters/registry.py` — `load_adapter_specs(countries)` bindt elke adapter aan expliciete country/site_id en valideert duplicates fail-fast; `load_adapter_classes(countries)` blijft beschikbaar voor class-only callers
- Retailerintegraties: `airco_tracker/adapters/nl/`, `airco_tracker/adapters/fr/`; voeg `adapters/<country>/` toe voor nieuwe landen
- CLI/orchestration: `airco_tracker/cli.py`
- State transitions: `airco_tracker/state.py`
- Inventory snapshot builder: `airco_tracker/inventory.py`
- State/inventory persistence: `airco_tracker/state_store.py`, `airco_tracker/inventory_store.py`
- Azure infrastructure: `infra/`
- Deploymentscripts: `scripts/`
- Tests: `tests/`
- Productie: Azure Container Apps scheduled job, private Blob Storage alert state/inventory, Communication Services Email, Key Vault en Managed Identity.
- CI/CD: push naar `main` draait tests, bouwt een commit-SHA-tagged immutable image, deployt en start één verification execution. Pure Markdown/docs-wijzigingen worden door de deployment workflow genegeerd.
- Consumer: `~/airco-tracking-web` serveert het publieke dashboard en leest de private snapshot via `/api/inventory` met de gedeelde runtime identity.

## Standaardverificatie

Run vanuit de repository-root:

```bash
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

De live dry-run doet netwerkreads maar mag geen mail sturen of state muteren. Als een lokaal geïnstalleerde `airco-tracker` entry point verouderd is, herinstalleer met `.venv/bin/pip install --no-deps --force-reinstall .` of gebruik `python -m airco_tracker`.

Voor inventory contract-wijzigingen moet je ook vanuit `~/airco-tracking-web` draaien:

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm typecheck
pnpm build
PORT=4174 INVENTORY_FILE=public/inventory.sample.json pnpm start
node scripts/verify-deployment.mjs http://127.0.0.1:4174
```

Werk de frontend server validator, browser types, fixture, tests en handoff in dezelfde gecoördineerde wijziging bij. Een groen backend-only testpakket is onvoldoende voor een schemawijziging.

## Wijzigingsworkflow

1. Inspecteer `git status` en recente geschiedenis.
2. Maak de kleinste coherente wijziging en voeg gerichte parser/state tests toe.
3. Draai unit tests, compile checks en `git diff --check`.
4. Voor retailerwijzigingen: voer een live `--dry-run` uit en inspecteer retailer counts/errors.
5. Voor inventory contract-wijzigingen: verifieer beide repositories en houd schema versioning expliciet.
6. Werk alle drie README's bij wanneer ondersteunde sites, configuratie of deploymentgedrag verandert.
7. Werk `docs/HANDOFF.md` en de taalvarianten bij wanneer huidige status, deployed commit, externe reviewstatus, frontendcontract, volgende taak of blockers veranderen.
8. Commit, push, deploy of start productiejobs alleen wanneer het gebruikersverzoek die acties autoriseert.

## Handoffkwaliteit

Houd `docs/HANDOFF.md` feitelijk en compact. Registreer datum, deployed commit, voltooid werk, actuele blocker, concrete volgende stappen en verificatie-evidence. Plaats er nooit secrets of onnodige persoonsgegevens in.
