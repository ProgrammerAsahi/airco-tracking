# Airco Tracker NL

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
  <a href="./README.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/README-Nederlands-f58220"></a>
</p>

A lightweight portable air-conditioner stock tracker for the Netherlands, with local execution and passwordless Azure deployment. It currently monitors:

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
- bol.com through the official Marketing Catalog API (Affiliate API credentials required)

It sends an email only when a product is first found available or changes from unavailable to available. It does not send the same notification every ten minutes. If one retailer fails, checks for the other retailers continue.

The bol.com search webpage is no longer scraped: Azure datacenter IP addresses receive HTTP 403 responses, and bol.com's robots.txt explicitly restricts that search path. Until official API credentials are configured, the bol.com adapter remains explicitly disabled while the other fourteen retailers continue normally.

EP.nl stock is read from server-rendered product cards. Electro World is read through the public, read-only product search index used by its own storefront, with the public search configuration discovered dynamically on every run. Wehkamp is read from the primary product data on its category page. None of these three integrations requires an account or secret credentials. Wehkamp removes sold-out products from the category, so an explicit empty category is a valid state; a restocked product triggers a first-seen availability alert as soon as it reappears.

Lidl's robots-restricted search route is not scraped. Products are discovered through Lidl's public product sitemap, after which JSON-LD availability is read from each real portable-air-conditioner page. GAMMA and KARWEI share a server-rendered product-tile parser, and only `ONLINE_AVAILABLE` counts as deliverable; store-only stock and collection-only products do not trigger alerts. Praxis checks both current availability and delivery modes, alerts only for products deliverable to a Dutch address, and excludes split air conditioners, air coolers, and accessories.

Alternate.nl, FlinQ, and Action Webshop discover new models through the product sitemaps published in their robots.txt files and then read availability from each product page. Action also keeps checking known expired seasonal deals so a reactivated URL is detected immediately. Trotec and Klarstein are read from server-rendered category product data. Trotec lead times of several weeks, presales, and merely orderable products do not count as immediate stock: only an explicit `Op voorraad` triggers an alert. Klarstein must expose an explicit online in-stock value. All five adapters exclude air coolers, fans, and accessories.

## Azure architecture

The production environment uses:

```text
Container Apps Scheduled Job
  ├─ Managed Identity → Blob Storage (stock state)
  ├─ Managed Identity → Communication Services Email (notifications)
  └─ Managed Identity → Key Vault (optional third-party credentials)
```

Azure mode stores no mailbox password, Storage key, Communication Services key, or ACR password. The recipient address and the BTU and price filters are not secrets and are supplied as normal configuration. Key Vault is reserved for third-party credentials that cannot be eliminated.

### Enable the official bol.com API

The bol Marketing Catalog API provides official product search, the best offer for the Netherlands, prices, and delivery descriptions. First join the bol Affiliate Program and create a Client ID and Client Secret in the Open API section of the Affiliate Portal. Never paste these credentials into source code, GitHub variables, or chat messages.

After the code has been deployed, run locally:

```bash
./scripts/configure-bol-api.sh
```

The script prompts for the Client Secret without displaying it, stores both credentials in Azure Key Vault, configures only non-sensitive GitHub Actions variables, and starts a deployment. At runtime, the container reads the secrets through Managed Identity.

## Run locally

### 1. Install

```bash
cd ~/airco-tracking-nl
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

Edit `.env` and enter the recipient email address and SMTP settings. Gmail users must enable two-step verification and create an app password; do not use the normal account password.

Optionally set `EMAIL_LANG` (default `zh`): `zh` for Chinese, `nl` for Dutch, `en` for English.

Run commands from the project directory. If you must run them elsewhere, set `AIRCO_TRACKER_HOME=~/airco-tracking-nl`.

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
tail -f ~/airco-tracking-nl/tracker.log ~/airco-tracking-nl/tracker.err.log
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
cd ~/airco-tracking-nl
EMAIL_TO=asahi.lee.eu@outlook.com ./scripts/deploy-azure.sh
```

The script:

1. Creates ACR, Blob Storage, Key Vault, a Container Apps Environment, Managed Identity, and Communication Services Email.
2. Builds the image remotely in ACR, so Docker is not required locally.
3. Creates a Container Apps Job that runs every ten minutes.
4. Starts one immediate execution to verify scraping and email delivery.

New Azure RBAC assignments can take a few minutes to propagate. If the first execution gets an ACR, Blob, or Communication Services 403, wait briefly and start it again:

```bash
az containerapp job start --name airco-tracker-job --resource-group airco-tracker-nl-rg
```

List executions and view logs:

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

The schedule uses UTC. `*/10 * * * *` runs every ten minutes and is unaffected by daylight saving time.

### Build the container locally (optional)

If Docker is installed:

```bash
./scripts/test-container.sh
```

`.dockerignore` explicitly excludes `.env`, state, and log files, so local credentials cannot enter the image.

### Optional Key Vault secret loading

Azure mode is passwordless by default, so Key Vault starts empty. If a retailer later requires an API key, create a Key Vault secret and configure:

```text
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net
KEY_VAULT_SECRET_MAP=PARTNER_API_KEY=partner-api-key
```

The application reads the secret through Managed Identity. The secret never enters source code, the image, or Bicep parameters.

## GitHub Actions CI/CD

The `ProgrammerAsahi/airco-tracking-nl` repository has two workflows:

- `.github/workflows/ci.yml`: validates Python, shell scripts, and Bicep on pull requests.
- `.github/workflows/deploy.yml`: after a successful test run on a `main` push, builds an immutable image tagged with the commit SHA and updates the Azure Job.

Azure authentication uses a short-lived GitHub OIDC token and no Client Secret. The federated identity trusts only the `main` branch of this repository and has Contributor access only to the target resource group. It cannot create role assignments or read application secrets from Key Vault.

### Initial setup order

Create the Azure foundation and OIDC trust locally before the first `main` push, so the workflow does not start before its variables exist:

```bash
brew install azure-cli gh
az login
gh auth login

cd ~/airco-tracking-nl
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

If `gh` is unavailable or not logged in, the bootstrap script prints the following five values. Add them manually under **Settings → Secrets and variables → Actions → Variables**:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP
EMAIL_TO
```

These values are identifiers or ordinary configuration, not passwords. Do not create or upload `AZURE_CREDENTIALS`, a Client Secret, or a subscription access token.

### First push

For an empty GitHub repository:

```bash
cd ~/airco-tracking-nl
git init -b main
git remote add origin https://github.com/ProgrammerAsahi/airco-tracking-nl.git
git add .
git commit -m "Initial airco tracker with Azure CI/CD"
git push -u origin main
```

`.env`, `.venv`, state, and log files are ignored by Git. Every later merge or push to `main` deploys once. Images use the complete Git commit SHA and never overwrite `latest`.

## Filters

Configure filters in `.env`:

- `MAX_PRICE_EUR=1500`: notify only about products costing at most €1,500. Products whose price is temporarily unavailable remain eligible to avoid missed alerts.
- `MIN_BTU=7000`: do not notify about products below 7,000 BTU. Genuine air conditioners whose BTU value is not present on the listing page are retained to avoid missed alerts.

## Maintenance and adding retailers

Each retailer has an independent adapter under `airco_tracker/adapters/`. Add a retailer by implementing an adapter and registering it in `cli.py`. If a page structure changes and no products can be parsed, the application reports `parser found no products` instead of silently pretending that everything is out of stock.

Keep the polling interval at ten minutes or longer. Product pages remain the final authority for stock, price, and delivery information.
