# Airco Tracker

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
  <a href="./README.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/README-Nederlands-f58220"></a>
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

French MVP sites:

- Boulanger
- Castorama
- Auchan
- Rue du Commerce
- Create France
- Evolarshop France
- Klarstein France
- Trotec France
- De'Longhi France
- Lidl France
- Action France

It sends an email only when a product is first found available or changes from unavailable to available. It does not send the same notification every ten minutes. If one retailer fails, checks for the other retailers continue.

French adapters also split immediate stock from presale inventory: `Pré-commande`, `Expédition à partir`, `livraison prévue semaine`, and multi-week lead times are shown as presale and do not trigger immediate-stock email alerts. Action France currently returns mostly coolers/fans for the query and is strictly filtered. Cdiscount, E.Leclerc, and the direct-403 French retailers are not enabled yet so anti-bot pages or JS shells are not treated as stock sources.

EP.nl stock is read from server-rendered product cards. Electro World is read through the public, read-only product search index used by its own storefront, with the public search configuration discovered dynamically on every run. Wehkamp is read from the primary product data on its category page. None of these three integrations requires an account or secret credentials. Wehkamp removes sold-out products from the category, so an explicit empty category is a valid state; a restocked product triggers a first-seen availability alert as soon as it reappears.

Lidl's robots-restricted search route is not scraped. Products are discovered through Lidl's public product sitemap, after which JSON-LD availability is read from each real portable-air-conditioner page. GAMMA and KARWEI share a server-rendered product-tile parser, and only `ONLINE_AVAILABLE` counts as deliverable; store-only stock and collection-only products do not trigger alerts. Praxis checks both current availability and delivery modes, alerts only for products deliverable to a Dutch address, and excludes split air conditioners, air coolers, and accessories.

Alternate.nl, FlinQ, and Action Webshop discover new models through the product sitemaps published in their robots.txt files and then read availability from each product page. Action also keeps checking known expired seasonal deals so a reactivated URL is detected immediately. Trotec and Klarstein are read from server-rendered category product data. Trotec lead times of several weeks, presales, and merely orderable products do not count as immediate stock: only an explicit `Op voorraad` triggers an alert. Klarstein must expose an explicit online in-stock value. All five adapters exclude air coolers, fans, and accessories.

Expert counts only products that can actually be ordered online; store-only stock never triggers an alert. De'Longhi reads the official JSON-LD on each product page and treats `Breng mij op de hoogte` as unavailable. Obelink and Kampeerwereld keep checking known seasonal products even when they disappear from category pages. Create treats both `Presale` and `Verzending vanaf` as unavailable until immediate dispatch is possible.

Costway NL reads the Magento category page's `qty-N` stock quantity; Evolarshop queries its public Nosto search API and excludes hoseless ("zonder afvoerslang") non-compressor units; Airco voor in huis uses the WooCommerce `instock`/`outofstock` status; Solago reads Shopify JSON-LD, where `Voorbestelling` and `Levering vanaf` pre-order text overrides the InStock schema as unavailable. Hubo has no airco category page and discovers portable air conditioners through its Shopify product sitemap; Vrijbuiter tracks portable split units for caravan and camper use (e.g. Mestic SPA, Qlima MS-AC), excluding air coolers and accessories. Klimaatshop is a specialist airco dealer whose product URLs are read from the `data-url` attribute and stock from the `.stock` span; Airco-Webwinkel is a WooCommerce store discovered via its product sitemap with JSON-LD detail pages.

Bostools reads both its WooCommerce mobile-airco and caravan-airco categories. `Leverbaar vanaf: date` is shown as presale inventory without sending email; explicit sold-out, collection-only, unboxed display items, and accessories never alert. Prices come from the consumer VAT-inclusive amount rather than the adjacent `excl. btw` business price.

Conrad.nl is not enabled yet: ordinary requests from both Azure and local execution receive Cloudflare HTTP 403. Conrad offers an official Price & Availability API through its Developer Portal, but access must be requested separately. This project does not bypass anti-bot protection.

## Azure architecture

The production environment uses:

```text
Container Apps Scheduled Job
  ├─ Managed Identity → Blob Storage (stock state)
  ├─ Managed Identity → Communication Services Email (notifications)
  └─ Managed Identity → Key Vault (recipient address and optional third-party credentials)
```

Azure mode stores no mailbox password, Storage key, Communication Services key, or ACR password. The recipient address is stored as the `notification-email` secret in Key Vault; GitHub stores only the `EMAIL_TO=notification-email` mapping. Price and BTU limits remain ordinary environment configuration.

## Run locally

### 1. Install

```bash
cd ~/airco-tracking
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

Edit `.env` and enter the recipient email address and SMTP settings. Gmail users must enable two-step verification and create an app password; do not use the normal account password.

Optionally set `EMAIL_LANG` (default `zh`): `zh` for Chinese, `nl` for Dutch, `en` for English.

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
EMAIL_TO=you@example.com ./scripts/deploy-azure.sh
```

The script:

1. Creates ACR, Blob Storage, Key Vault, a Container Apps Environment, Managed Identity, and Communication Services Email.
2. Builds the image remotely in ACR, so Docker is not required locally.
3. Creates a Container Apps Job that runs every ten minutes.
4. Starts one immediate execution to verify scraping and email delivery.

New Azure RBAC assignments can take a few minutes to propagate. If the first execution gets an ACR, Blob, or Communication Services 403, wait briefly and start it again:

```bash
az containerapp job start --name airco-tracker-job --resource-group airco-tracker-rg
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

To replace the production notification address without committing it or storing it in GitHub, run:

```bash
./scripts/configure-notification-email.sh
```

The script reads the existing GitHub value during migration or prompts without echo, stores it as `notification-email` in Key Vault, and removes the old GitHub value. If a retailer later requires an API key, create another Key Vault secret and extend the mapping:

```text
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net
KEY_VAULT_SECRET_MAP=PARTNER_API_KEY=partner-api-key
```

The application reads the secret through Managed Identity. The secret never enters source code, the image, or Bicep parameters.

## GitHub Actions CI/CD

The `ProgrammerAsahi/airco-tracking` repository has two workflows:

- `.github/workflows/ci.yml`: validates Python, shell scripts, and Bicep on pull requests.
- `.github/workflows/deploy.yml`: after a successful test run on a `main` push, builds an immutable image tagged with the commit SHA and updates the Azure Job.

Azure authentication uses a short-lived GitHub OIDC token and no Client Secret. The federated identity trusts only the `main` branch of this repository and has only the custom `Airco GitHub Deployer Minimal` role needed for deployment. It does not have target resource-group Contributor access, cannot create role assignments, and cannot read application secrets from Key Vault.

### Initial setup order

Create the Azure foundation and OIDC trust locally before the first `main` push, so the workflow does not start before its variables exist:

```bash
brew install azure-cli gh
az login
gh auth login

cd ~/airco-tracking
EMAIL_TO=you@example.com ./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

If `gh` is unavailable or not logged in, the bootstrap script prints the following six values. Add them manually under **Settings → Secrets and variables → Actions → Variables**:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP
EMAIL_LANG
KEY_VAULT_SECRET_MAP
```

These values are identifiers or ordinary configuration, not passwords. Do not create or upload `AZURE_CREDENTIALS`, a Client Secret, or a subscription access token.

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

`.env`, `.venv`, state, and log files are ignored by Git. Every later merge or push to `main` deploys once. Images use the complete Git commit SHA and never overwrite `latest`.

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
