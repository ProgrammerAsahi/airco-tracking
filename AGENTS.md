# Airco Tracker — shared agent instructions

<p align="center">
  <a href="./AGENTS.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/AGENTS-简体中文-d73a49"></a>
  <a href="./AGENTS.md"><img alt="English" src="https://img.shields.io/badge/AGENTS-English-0969da"></a>
</p>

## Mission

Maintain a reliable, scalable stock tracker for genuine portable compressor air conditioners deliverable to supported countries, currently France and the Netherlands. Notify entitled subscribers only when a product is newly available or changes from unavailable to available, and publish a trustworthy private inventory snapshot for the authenticated web experience.

## Read first

1. Read `docs/HANDOFF.md` for the current state and next task.
2. Read the relevant adapter, tests, and infrastructure files before editing.
3. Use the language-specific README only for user-facing setup details (`README.md`, `README.en.md`). Keep both synchronized when behavior changes.
4. If inventory schema or semantics are involved, inspect `~/airco-tracking-web/server/inventory.ts`, `src/types.ts`, its fixture/tests, and the frontend handoff before editing.
5. All Markdown documentation must be maintained in Chinese and English. When changing any doc, update both language variants in the same change.

## Non-negotiable rules

- Never commit or print API keys, client secrets, passwords, access tokens, SMTP credentials, or Key Vault secret values.
- Third-party credentials belong in Azure Key Vault and are read through Managed Identity.
- Prefer official APIs. Otherwise use public server-rendered pages or robots-advertised sitemaps. Respect robots.txt and terms; never bypass CAPTCHA, 403 protections, login barriers, or anti-bot controls.
- Track genuine compressor air conditioners. Exclude air coolers, evaporative coolers, fans, hoses, window kits, remotes, filters, and other accessories.
- `available=True` means currently orderable for delivery to the requested supported country. Store-only stock, pickup-only stock, expired deals, presales, and multi-week lead times must not trigger alerts.
- One retailer failure must not stop the remaining retailers. Do not turn a failed check into an out-of-stock transition.
- Tests and dry-runs must not send email or update production state.
- Keep alert state and live inventory separate. Alert filters must not remove otherwise in-scope available products from `inventory.json`.
- `inventory.json` schema version `1` is consumed in production by `airco-tracking-web`. Do not silently change fields, meanings, or failure/staleness behavior without a coordinated cross-repository change and tests.
- Keep the `airco-tracker` Blob container private. Never expose Storage credentials or a SAS URL to browser code; the frontend's same-origin Node API reads it with Managed Identity.
- Do not read order, buyer, payment, or other personal data when product-catalog and affiliate-offer scopes are sufficient.
- Preserve unrelated user changes in a dirty worktree.

## Architecture

- Python package: `airco_tracker/`
- Country-agnostic parsing helpers: `airco_tracker/adapters/base.py` (the `Adapter` ABC, price/BTU/presale parsing), `schema.py` (JSON-LD), `sitemap.py`
- Country-based adapter registry: `airco_tracker/adapters/registry.py` — `load_adapter_specs(countries)` binds each adapter to an explicit country/site_id and fail-fast validates duplicates; `load_adapter_classes(countries)` remains available for class-only callers
- Retailer integrations: `airco_tracker/adapters/nl/` (Dutch retailers); add `adapters/<country>/` for new countries
- CLI/orchestration: `airco_tracker/cli.py`
- State transitions: `airco_tracker/state.py`
- Inventory snapshot builder: `airco_tracker/inventory.py`
- State/inventory persistence: `airco_tracker/state_store.py`, `airco_tracker/inventory_store.py`
- Azure infrastructure: `infra/`
- Deployment scripts: `scripts/`
- Tests: `tests/`
- Production: isolated Azure Container Apps jobs for scanning, reconciliation, publication, fanout, email delivery, delivery reports, and retention; private Blob/Table Storage; partitioned Azure Service Bus; Communication Services Email; Key Vault; and dedicated least-privilege Managed Identities.
- CI/CD: a push to `main` runs tests, builds an immutable image tagged with the commit SHA, deploys it, and starts one verification execution.
- Consumer: `~/airco-tracking-web` serves the authenticated inventory experience and reads the private snapshot through its same-origin `/api/inventory` endpoint using its own runtime identity.

## Standard verification

Run from the repository root:

```bash
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

The live dry-run performs network reads but must not send mail or mutate state. If a local installed `airco-tracker` entry point is stale, reinstall with `.venv/bin/pip install --no-deps --force-reinstall .` or use `python -m airco_tracker`.

For inventory contract changes, also run from `~/airco-tracking-web`:

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm typecheck
pnpm build
PORT=4174 INVENTORY_FILE=public/inventory.sample.json pnpm start
node scripts/verify-deployment.mjs http://127.0.0.1:4174
```

Update the frontend server validator, browser types, fixture, tests, and handoff in the same coordinated change. A backend-only green test suite is insufficient for a schema change.

## Change workflow

1. Inspect `git status` and recent history.
2. Make the smallest coherent change and add focused parser/state tests.
3. Run unit tests, compile checks, and `git diff --check`.
4. For retailer changes, perform a live `--dry-run` and inspect retailer counts/errors.
5. For inventory contract changes, verify both repositories and keep schema versioning explicit.
6. Update both READMEs when supported sites, configuration, or deployment behavior changes.
7. Update `docs/HANDOFF.md` whenever current status, deployed commit, external review state, frontend contract, next task, or blockers change.
8. Commit, push, deploy, or start production jobs only when the user's request authorizes those actions.

## Handoff quality

Keep `docs/HANDOFF.md` factual and compact. Record the date, deployed commit, completed work, current blocker, next concrete steps, and verification evidence. Never place secrets or unnecessary personal data in it.
