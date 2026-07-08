# Airco Tracker — 当前交接

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
  <a href="./HANDOFF.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/HANDOFF-Nederlands-f58220"></a>
</p>

最后更新：2026-07-08（Europe/Amsterdam）

文档规则：当前状态、验证证据、blocker 或下一步变化时，必须同时更新中文、英语和荷兰语 handoff。

## 当前目标

运行一个可靠、低维护成本的便携空调库存追踪器，追踪可配送到荷兰和法国地址的商品，并保留面向更多欧洲国家扩展的国家化架构。生产环境每 10 分钟在 Azure 中运行一次，维护当前库存快照供公开 dashboard 读取，并且只在商品首次出现或从不可购买变为可购买时发送邮件提醒。

项目已从 `airco-tracking-nl` 迁移并更名为 `airco-tracking`。Adapter 现在按国家组织：`airco_tracker/adapters/nl/`、`airco_tracker/adapters/fr/` 和共享逻辑 `airco_tracker/adapters/shared/`。`COUNTRIES` 环境变量控制 active countries，生产当前为 `nl,fr`。

## 仓库和生产

- Repository：`https://github.com/ProgrammerAsahi/airco-tracking`
- Branch：`main`
- Local path：`~/airco-tracking`
- GitHub workflow：`Deploy to Azure`
- Azure resource group：`airco-tracker-rg`
- Container Apps job：`airco-tracker-job`
- Schedule：`*/10 * * * *` (UTC)
- Alert state：Azure Blob Storage，`airco-tracker/state.json`
- Live inventory：Azure Blob Storage，`airco-tracker/inventory.json`
- Notifications：Azure Communication Services Email
- Dashboard consumer：`https://github.com/ProgrammerAsahi/airco-tracking-web`
- Dashboard live URL：`https://airco-tracker.eu/`
- Deployment workflow：纯 Markdown/docs 改动已被 `paths-ignore` 忽略，不会触发生产部署。

不要在本文件记录 secrets、邮箱地址、token、password 或不必要的个人信息。

## Active retailers

应用当前注册 45 个无需凭据的 active adapters：28 个荷兰站点和 17 个法国站点。

荷兰 adapters：

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

法国 adapters：

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

Deferred 法国 adapters：

- Boulanger：本地/GitHub-hosted 请求可读 server-rendered search page，但 Azure Container Apps 出站请求稳定 60 秒 read timeout。Parser 保留在 `adapters/fr/boulanger.py`，找到稳定 API 或官方/公开替代源前不要注册。
- Brico Dépôt France：本地可读 category JSON-LD 和 Fasterize/smartcache fragment，但 Azure Container Apps 收到过小/不可用响应。Parser 保留在 `adapters/fr/bricodepot.py`，找到稳定数据源前不要注册。
- Direct 403/anti-bot backlog 见 `docs/RETAILER_403_BACKLOG.md` 及其语言版本。

## 关键语义

- 只追踪真实压缩机空调。排除 air coolers、风扇、除湿机、窗户套件、软管、配件、固定 split systems 和 quote-only listings。
- `available=True` 表示当前可在线配送到目标国家地址。仅门店库存、仅自取、过期 deals、多周交期和预售不得触发现货邮件。
- 预售商品可以进入 dashboard，但不触发现货提醒；从预售转为即时现货应触发提醒。
- 单个零售商失败不得阻断其它零售商。失败站点保留上次成功库存并标记 `status: error` / `stale: true`。
- Live inventory 与 alert state 分离。库存快照不应用价格、BTU 或 brand alert filters；这些 filters 只影响邮件提醒。
- `inventory.json` schema version `1` 是前后端生产契约。Breaking change 必须协调两个仓库并显式 bump schema。
- Blob container 必须保持私有；浏览器只能通过前端同源 API 读取。

## 配置和 secret model

- `MAX_PRICE_EUR=1500`
- `MIN_BTU=7000`
- `COUNTRIES=nl,fr` in production
- `EMAIL_LANG=zh` in production (`zh`, `nl`, `en` supported)
- 生产 recipient 存在 Key Vault secret `notification-email`。
- GitHub 只存 `KEY_VAULT_SECRET_MAP=EMAIL_TO=notification-email`，不存真实地址。
- 第三方 credentials 必须进入 Key Vault，并通过 Managed Identity 读取。
- SMTP credentials 只允许本地 `.env`；Azure 使用 passwordless Communication Services。

## 前端 contract

- 后端是私有 `airco-tracker/inventory.json` 的唯一 producer。
- 前端 `airco-tracking-web` 通过同源 `/api/inventory` 和 runtime Managed Identity 读取 Blob。
- 前端根据 URL state `/deliver-to/nl`、`/deliver-to/fr` 过滤目标配送国家；显示语言是独立 query/user preference state。
- `delivery_coverage` 是 site-level metadata。当前 widened coverage 仅在有官方 policy-page evidence 时添加。
- Schema 或语义变化必须同时更新：后端 producer/tests、前端 validator/tests、browser types/UI、fixture、README 和两个 handoff。

## AliExpress / Conrad 状态

- Conrad.nl 未注册。其普通页面对项目请求返回 Cloudflare 403；官方 Price & Availability API 需要 allowlist/审批。不要绕过反爬。
- AliExpress affiliate account 已通过，但 Open Platform/API 状态需要在 portal 重新确认后再开发。只允许使用官方 Affiliate/Open Platform API，不保留 buyer、order、payment 或其它个人数据。

## 候选下一步

这些是选项，不代表自动授权：

1. France 403/API backlog：继续为 Leroy Merlin、Darty、ManoMano、Fnac、Carrefour、Cdiscount、E.Leclerc、BUT、Conforama、Ubaldi、Bricomarché、Mr.Bricolage、Weldom、Qlima、Rakuten France 和 La Redoute 寻找稳定官方/公开数据路径。
2. Conrad API：等待 allowlist/approval；开发前先检查 Developer Portal。
3. AliExpress API：重新检查 portal 状态，确认 app key/secret 与官方签名流程后再实现。

## 标准本地验证

```bash
cd ~/airco-tracking
.venv/bin/pip install --no-deps --force-reinstall .
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

库存 contract 变化还必须验证前端（见 `~/airco-tracking-web/HANDOFF.md`）。

## 部署命令（需要授权）

```bash
# Backend: push to main triggers .github/workflows/deploy.yml automatically.
IMAGE_TAG=$(git -C ~/airco-tracking rev-parse --short=12 HEAD) \
  AZURE_RESOURCE_GROUP=airco-tracker-rg \
  ~/airco-tracking/scripts/deploy-application.sh

# Trigger a verification job execution:
az containerapp job start -n airco-tracker-job -g airco-tracker-rg
```

## 更新本 handoff

替换过期状态，而不是追加流水账。始终记录 deployed commit、active retailer count、external API review state、frontend contract compatibility、精确验证证据和下一项具体行动。不要包含邮箱地址、secret 值、token、password 或不必要的个人信息。
