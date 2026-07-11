# Airco Tracker

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
</p>

A lightweight portable air-conditioner stock tracker for the Netherlands and France, with local execution and passwordless Azure deployment. It currently monitors these Dutch sites:

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

Enabled French sites:

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

Boulanger and Brico Dépôt France adapter code is kept in the repository but is not registered in production yet: Boulanger is reachable locally, while Azure Container Apps outbound requests consistently hit a 60-second read timeout; Brico Dépôt's normal category page and smartcache fragment are readable locally, but Azure outbound requests receive too-small/unusable responses. Enable either one only after a stable page API or official/public alternative source is found.

It sends an email only when a product is first found available or changes from unavailable to available. It does not send the same notification every ten minutes. If one retailer fails, checks for the other retailers continue.

French adapters also split immediate stock from presale inventory: `Pré-commande`, `Expédition à partir`, `livraison prévue semaine`, `Sur commande`, and multi-week lead times are shown as presale and do not trigger immediate-stock email alerts. Electro Dépôt France reads the embedded Vue JSON `stock` value; Costway France reads the Magento `qty-N` stock class and `Précommande` labels while excluding split systems, air coolers, and accessories; Maison Energy lets `Non disponible`/`Demande de devis` override schema `PreOrder` so quote-only products do not alert as stock. H2R Équipements reads only the camper/van `climatisation nomade` category, where only `En stock` is immediate and `Sur commande`/`Retour en stock prévu` never alerts as stock; Obelink France reads public JSON-LD category and product data for mobile/split camping air conditioners; Narbonne Accessoires trusts the visible `Livraison à Domicile` block rather than schema stock so store-only pickup is not reported as delivery stock; Mon Camping Car displays `BackOrder`/`Disponible à partir` products as presale. Action France currently returns mostly coolers/fans for the query and is strictly filtered. Boulanger, Brico Dépôt France, Cdiscount, E.Leclerc, and the direct-403 French retailers are not enabled yet so timeouts, anti-bot pages, or JS shells are not treated as stock sources.

EP.nl stock is read from server-rendered product cards. Electro World is read through the public, read-only product search index used by its own storefront, with the public search configuration discovered dynamically on every run. Wehkamp is read from the primary product data on its category page. None of these three integrations requires an account or secret credentials. Wehkamp removes sold-out products from the category, so an explicit empty category is a valid state; a restocked product triggers a first-seen availability alert as soon as it reappears.

Lidl's robots-restricted search route is not scraped. Products are discovered through Lidl's public product sitemap, after which JSON-LD availability is read from each real portable-air-conditioner page. GAMMA and KARWEI normally share a server-rendered product-tile parser. When their category host rate-limits Azure, the adapters use the read-only catalogue query published by the storefront and require its online-purchase, temporary-out-of-stock, quantity, stock, and availability fields to agree. Their robots-declared product sitemaps are only a fail-safe for proving that a current catalogue has no portable-air-conditioner candidates; sitemap membership is never treated as stock. Store-only and collection-only products do not trigger alerts. Praxis checks both current availability and delivery modes, alerts only for products deliverable to a Dutch address, and excludes split air conditioners, air coolers, and accessories.

Alternate.nl, FlinQ, and Action Webshop discover new models through the product sitemaps published in their robots.txt files and then read availability from each product page. Action also keeps checking known expired seasonal deals so a reactivated URL is detected immediately. Trotec and Klarstein are read from server-rendered category product data. Trotec lead times of several weeks, presales, and merely orderable products do not count as immediate stock: only an explicit `Op voorraad` triggers an alert. Klarstein must expose an explicit online in-stock value. All five adapters exclude air coolers, fans, and accessories.

Expert counts only products that can actually be ordered online; store-only stock never triggers an alert. De'Longhi reads the official JSON-LD on each product page and treats `Breng mij op de hoogte` as unavailable. Obelink and Kampeerwereld keep checking known seasonal products even when they disappear from category pages. Create treats both `Presale` and `Verzending vanaf` as unavailable until immediate dispatch is possible.

Costway NL reads the Magento category page's `qty-N` stock quantity; Evolarshop queries its public Nosto search API and excludes hoseless ("zonder afvoerslang") non-compressor units; Airco voor in huis uses the WooCommerce `instock`/`outofstock` status; Solago reads Shopify JSON-LD, where `Voorbestelling` and `Levering vanaf` pre-order text overrides the InStock schema as unavailable. Hubo has no airco category page and discovers portable air conditioners through its Shopify product sitemap; Vrijbuiter tracks portable split units for caravan and camper use (e.g. Mestic SPA, Qlima MS-AC), excluding air coolers and accessories. Klimaatshop is a specialist airco dealer whose product URLs are read from the `data-url` attribute and stock from the `.stock` span; Airco-Webwinkel is a WooCommerce store discovered via its product sitemap with JSON-LD detail pages.

Bostools reads both its WooCommerce mobile-airco and caravan-airco categories. `Leverbaar vanaf: date` is shown as presale inventory without sending email; explicit sold-out, collection-only, unboxed display items, and accessories never alert. Prices come from the consumer VAT-inclusive amount rather than the adjacent `excl. btw` business price.

Conrad.nl is not enabled yet: ordinary requests from both Azure and local execution receive Cloudflare HTTP 403. Conrad offers an official Price & Availability API through its Developer Portal, but access must be requested separately. This project does not bypass anti-bot protection.

## Azure architecture

Production alerts are split from the scanner into an asynchronous pipeline:

```text
Scanner Job (every 10 minutes)
  └─ Table outbox → Service Bus topic
       └─ 32 recipient-shard jobs
            └─ one email job per entitled subscriber
                 └─ idempotent delivery ledger → ACS Email
```

The scanner only fetches retailers, updates private `state.json`/`inventory.json`, and persists deterministic stock events. It never enumerates users or waits for mail. Container Apps workers scale independently from Azure Service Bus Standard backlog; the recipient projection is spread across 32 Azure Table partitions and streamed page by page, so subscriber growth does not slow inventory scans.

Production uses separate Managed Identities for the scanner/shared web runtime, outbox publisher, fan-out, and email delivery; new pipeline access is narrowed to the relevant entity/table wherever possible. Service Bus messages carry opaque user UUIDs rather than email, nickname, Stripe/payment, or card data. The email worker reloads the current email, language, delivery country, and entitlement immediately before sending. `EMAIL_TO` belongs only to local direct/SMTP mode and is not a production subscriber source.

Azure Monitor has four enabled Service Bus metric alerts for dead-lettered messages, sustained backlog, throttled requests, and server errors. In production they notify the `aircontrack-operations-alerts` Action Group. The receiver address is supplied only as the secure foundation parameter `operationsAlertEmail`, normally through local `AZURE_OPERATIONS_ALERT_EMAIL`; it is never committed or stored as a GitHub Actions variable. Repeat foundation deployments preserve the Action Group's existing receiver when the environment variable is omitted.

See [asynchronous alert pipeline](./docs/ALERT_PIPELINE.md) for the full topology, idempotency model, configuration, retention, scaling limits, deployment order, and runbook. DNS, verification, rollout, real-delivery checks, and rollback for the custom ACS sender are documented in the [ACS custom email domain runbook](./docs/ACS_CUSTOM_EMAIL_DOMAIN.md).

## Run locally

### 1. Install

```bash
cd ~/airco-tracking
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

Edit `.env` and enter the recipient email address and SMTP settings. Gmail users must enable two-step verification and create an app password; do not use the normal account password.

Optionally set `EMAIL_LANG` (default `zh`): `zh` for Chinese, `nl` for Dutch, `en` for English, or `fr` for French. Production alerts use the language saved in the recipient's Profile and localise the destination country and price format for that recipient.

Run commands from the project directory. If you must run them elsewhere, set `AIRCO_TRACKER_HOME=~/airco-tracking`.

### 2. Verify

Check page parsing without sending email or updating state:

```bash
.venv/bin/airco-tracker check --dry-run --show-all
```

Send a test email:

```bash
.venv/bin/airco-tracker send-test
```

Check backend configuration and state access without sending email:

```bash
.venv/bin/airco-tracker doctor
```

Finally, run one real check:

```bash
.venv/bin/airco-tracker check
```

By default, the first real run reports products that are already available. Later runs notify only about newly available stock. Set `ALERT_ON_FIRST_SEEN=false` in `.env` to suppress the initial notification.

### 3. Run in the background on macOS

```bash
./install-launch-agent.sh
```

The macOS LaunchAgent checks every ten minutes and resumes after login. View logs with:

```bash
tail -f ~/airco-tracking/tracker.log ~/airco-tracking/tracker.err.log
```

Stop it with:

```bash
./uninstall-launch-agent.sh
```

## Deploy to Azure

Requirements:

- An active Azure subscription.
- Azure CLI with `az login` completed.
- Permission to create resource groups, role assignments, and the required Azure resources.

Deploy with:

```bash
cd ~/airco-tracking
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

The script:

1. Registers required Azure resource providers and creates ACR, Storage, Key Vault, a Container Apps Environment, Service Bus Standard, ACS Email, and responsibility-separated Managed Identities.
2. Creates the `alertoutbox`, `alertrecipients`, and `alertdeliveries` Tables, topic/subscription, two queues, four Service Bus metric alerts, and the configured operations Action Group.
3. Builds the image remotely in ACR, so Docker is not required locally.
4. Deploys scanner/publisher/reconciler/retention jobs and three backlog-scaled worker apps.
5. Verifies recipient reconciliation, scanner, and outbox publisher in dependency order. Real mail is verified separately with a targeted `pipeline-test`; deployment never broadcasts a test to all users.

The foundation creates RBAC and must be run by a local Azure principal allowed to create role assignments. The GitHub deployer deliberately lacks this permission and deploys only the application layer. New RBAC assignments can take a few minutes to propagate. If the first application execution gets an ACR, Storage, Service Bus, or ACS 403, wait and redeploy the application:

```bash
AZURE_RESOURCE_GROUP=airco-tracker-rg ./scripts/deploy-application.sh
```

List executions and view logs:

```bash
az containerapp job execution list \
  --name airco-tracker-job \
  --resource-group airco-tracker-rg \
  --output table

az containerapp job logs show \
  --name airco-tracker-job \
  --resource-group airco-tracker-rg \
  --follow
```

The schedule uses UTC. `*/10 * * * *` runs every ten minutes and is unaffected by daylight saving time.

### Build the container locally (optional)

If Docker is installed:

```bash
./scripts/test-container.sh
```

`.dockerignore` explicitly excludes `.env`, state, and log files, so local credentials cannot enter the image.

### Key Vault secret loading

Production fan-out recipients come from the `alertrecipients` Table synchronized by the web service. A Profile email change preserves the stable UUID, and the email worker point-reads the canonical UUID-keyed user before every send, so a lagging projection cannot send to the old address. Production does not read `notification-email` or `EMAIL_TO` as a fallback: if the canonical account cannot be checked, delivery fails closed.

Key Vault is reserved for future secret credentials required by third-party adapters. The container may read them through Managed Identity and a mapping such as:

```text
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net
KEY_VAULT_SECRET_MAP=PARTNER_API_KEY=partner-api-key
```

Secrets never enter source code, the image, Bicep parameters, or Service Bus messages. Change subscriber addresses through the web Profile, not Key Vault or deployment variables.

## GitHub Actions CI/CD

The `ProgrammerAsahi/airco-tracking` repository has two workflows:

- `.github/workflows/ci.yml`: validates Python, shell scripts, and Bicep on pull requests.
- `.github/workflows/deploy.yml`: after a successful test run on an eligible `main` push, builds an immutable image tagged with the commit SHA and updates the scanner/publisher/reconciler/retention jobs plus the fan-out and email worker apps.

Azure authentication uses a short-lived GitHub OIDC token and no Client Secret. The federated identity trusts only the `main` branch of this repository and has only the custom `Airco GitHub Deployer Minimal` role needed for deployment. It does not have target resource-group Contributor access, cannot create role assignments, and cannot read application secrets from Key Vault.

### Initial setup order

Create the Azure foundation and OIDC trust locally before the first `main` push, so the workflow does not start before its variables exist:

```bash
brew install azure-cli gh
az login
gh auth login

cd ~/airco-tracking
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

If `gh` is unavailable or not logged in, the bootstrap script prints the following ten values. Add them manually under **Settings → Secrets and variables → Actions → Variables**:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP
AZURE_PREFIX
EMAIL_LANG
EMAIL_MIN_SEND_INTERVAL_SECONDS
EMAIL_MAX_REPLICAS
ACS_EMAIL_DOMAIN_NAME
DEPLOYMENT_PAUSED
```

These values are identifiers or ordinary configuration, not passwords. `DEPLOYMENT_PAUSED` is normally `false`; setting it temporarily to `true` for a maintenance window preserves the paused scanner/publisher schedules and skips their active verification executions. Do not create or upload `AZURE_CREDENTIALS`, a Client Secret, or a subscription access token.

### First push

For an empty GitHub repository:

```bash
cd ~/airco-tracking
git init -b main
git remote add origin https://github.com/ProgrammerAsahi/airco-tracking.git
git add .
git commit -m "Initial airco tracker with Azure CI/CD"
git push -u origin main
```

`.env`, `.venv`, state, and log files are ignored by Git. Every eligible non-documentation push to `main` deploys once; documentation-only and deployment-workflow-only changes are ignored and can be released with `workflow_dispatch` when needed. Images use the complete Git commit SHA and never overwrite `latest`.

## Filters

Configure filters in `.env`:

- `MAX_PRICE_EUR=1500`: notify only about products costing at most €1,500. Products whose price is temporarily unavailable remain eligible to avoid missed alerts.
- `MIN_BTU=7000`: do not notify about products below 7,000 BTU. Genuine air conditioners whose BTU value is not present on the listing page are retained to avoid missed alerts.

## Live inventory snapshot

Every production check writes a separate `inventory.json` grouped by retailer, containing all currently online-purchasable portable compressor air conditioners. Price, BTU, and brand alert filters do not apply to this snapshot; adapter-level exclusions for air coolers, fans, accessories, and fixed split systems still apply. Email remains limited to first-seen or restocked products that pass the alert filters above.

A successful retailer check fully replaces that retailer's inventory, including with zero products. A failed check retains the last successful inventory and marks the retailer `stale: true` with `status: error`. Local mode writes `inventory.json` in the project directory; Azure writes the same blob name to the existing `airco-tracker` container, without new cloud resources. `--dry-run` writes neither inventory nor alert state.

## Maintenance and adding retailers

Each retailer has an independent adapter under `airco_tracker/adapters/<country>/`. Add a retailer by implementing an adapter, registering it in that country's `ADAPTERS` list and `adapters/registry.py`, and maintaining conservative `delivery_coverage` metadata for the site (ISO-2 country codes or the `eu`/`eea`/`nordics`/`benelux`/`dach` region aliases). If a page structure changes and no products can be parsed, the application reports `parser found no products` instead of silently pretending that everything is out of stock.

Keep the polling interval at ten minutes or longer. Product pages remain the final authority for stock, price, and delivery information.

## Documentation language maintenance

All Markdown documentation should have Chinese and English versions with language-switch badges at the top. Whenever any document changes, update both language versions together.
