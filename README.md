# Airco Tracker

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
  <a href="./README.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/README-Nederlands-f58220"></a>
</p>

一个轻量的荷兰/法国便携空调库存追踪器，支持本地运行和无密码 Azure 部署。当前监控荷兰站点：

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
- De’Longhi 荷兰直营店
- Obelink
- Kampeerwereld
- Create 荷兰站
- Costway NL
- Evolarshop
- Airco voor in huis
- Solago
- Hubo
- Vrijbuiter
- Klimaatshop
- Airco-Webwinkel
- Bostools

法国已启用站点：

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
- De’Longhi France
- Lidl France
- Action France

Boulanger 和 Brico Dépôt France 的适配器代码已保留，但暂未在生产注册：Boulanger 页面本地可访问，而 Azure Container Apps 出站请求会稳定卡到 60 秒读取超时；Brico Dépôt 的普通分类页和 smartcache fragment 本地可读，但 Azure 出站请求会返回过小/不可用响应。两者都需要找到稳定的页面内 API 或官方/公开替代源后再启用。

它只在商品首次被发现为可购买，或从缺货变为有货时发送邮件；不会每 10 分钟轰炸邮箱。单个零售商失效时，其余站点仍会继续检查。

法国站点同样区分即时现货和预售：`Pré-commande`、`Expédition à partir`、`livraison prévue semaine` 和多周交期会显示为预售库存，但不会触发现货邮件。Electro Dépôt France 读取页面内 Vue JSON 的 `stock` 数字；Costway France 读取 Magento 分类页的 `qty-N` 库存和 `Précommande` 标签，并排除 split、冷风机和配件；Maison Energy 会让 `Non disponible`/`Demande de devis` 优先于 schema `PreOrder`，避免不可下单商品触发现货。Action France 当前搜索结果主要是冷风机/风扇，因此会被严格过滤；Boulanger、Brico Dépôt France、Cdiscount、E.Leclerc 以及直接 403 的法国站点暂未启用，避免把超时、反爬页或 JS 壳误当库存来源。

EP.nl 通过服务器输出的商品卡识别在线库存；Electro World 使用其网页公开调用的只读商品搜索索引，并在每次运行时动态读取公开搜索配置；Wehkamp 读取分类页的主商品数据。三者均不需要账号或秘密凭据。Wehkamp 会把售罄商品从分类移除，因此明确的空分类是正常状态；商品补货并重新出现时会立即触发首次有货提醒。

Lidl 不抓取 robots.txt 禁止的搜索路径，而是通过其公开商品 sitemap 发现真正的移动空调，再读取商品页的 JSON-LD 库存。GAMMA 和 KARWEI 共用服务器商品卡解析器，只有 `ONLINE_AVAILABLE` 才算可配送；仅门店库存或仅自取不会提醒。Praxis 同时检查当前可用性和送货方式，只有支持荷兰地址配送的商品才会提醒，并排除 split airco、aircooler 和配件。

Alternate.nl、FlinQ 和 Action Webshop 通过 robots.txt 中公布的商品 sitemap 自动发现新型号，再读取商品页库存；Action 还持续检查已知的过期季节商品，以便原链接重新上架时立即发现。Trotec 和 Klarstein 读取服务器输出的分类商品数据。Trotec 的数周交期、预售或“可加入购物车”不会被误判成即时现货，只有明确的 `Op voorraad` 才会提醒；Klarstein 只接受其明确的在线库存字段。五家网站均会排除 aircooler、风扇和空调配件。

Expert 只有明确可在线下单时才算有货，仅门店库存不会提醒；De’Longhi 读取商品页官方 JSON-LD，`Breng mij op de hoogte` 视为缺货；Obelink 和 Kampeerwereld 会持续检查已知季节商品，即使它们暂时从分类页消失；Create 的 `Presale` 和 `Verzending vanaf` 都不会触发提醒。

Costway NL 读取 Magento 分类页的 `qty-N` 库存数量；Evolarshop 通过其公开的 Nosto 搜索 API 获取商品，并排除"无排气管"（zonder afvoerslang）的非压缩机产品；Airco voor in huis 使用 WooCommerce 的 `instock`/`outofstock` 状态；Solago 读取 Shopify JSON-LD，`Voorbestelling` 和 `Levering vanaf` 预售文本会覆盖 InStock 标记为缺货。Hubo 没有 airco 分类页，通过 Shopify 商品 sitemap 发现便携空调并读取 JSON-LD 库存；Vrijbuiter 追踪露营和房车用便携式分体空调（如 Mestic SPA、Qlima MS-AC），排除 aircooler 和配件。Klimaatshop 是专业空调商，从 `data-url` 属性提取产品、`.stock` span 判断库存；Airco-Webwinkel 通过 sitemap 发现产品并读取 WooCommerce JSON-LD 库存。

Bostools 同时读取 WooCommerce 的移动空调和房车空调分类。`Leverbaar vanaf: 日期` 作为预售显示在网页但不发邮件；明确售罄、仅自取、无包装展示品和配件不会触发提醒。价格只读取面向消费者的含税价，不会误取旁边的 `excl. btw` 企业价。

Conrad.nl 暂未启用：普通网页从 Azure 和本地请求都会收到 Cloudflare 403。Conrad 官方 Developer Portal 提供 Price & Availability API，但需要单独申请访问；本项目不会绕过其反爬保护。

## Azure 架构

生产环境采用：

```text
Container Apps Scheduled Job
  ├─ Managed Identity → Blob Storage（提醒状态 + 实时库存快照）
  ├─ Managed Identity → Communication Services Email（通知）
  └─ Managed Identity → Key Vault（收件地址和第三方密钥）
```

Azure 模式不保存邮箱密码、Storage Key、Communication Services Key 或 ACR 密码。收件地址作为 `notification-email` secret 存在 Key Vault；GitHub 只保存 `EMAIL_TO=notification-email` 映射。价格和 BTU 限制作为普通环境配置传入。

## 本地运行

### 1. 安装

```bash
cd ~/airco-tracking
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

编辑 `.env`，填入收件邮箱和 SMTP。Gmail 用户需要开启两步验证，并创建一个“应用专用密码”；不要填写日常登录密码。

可选设置 `EMAIL_LANG`（默认 `zh`）：`zh` 发中文、`nl` 发荷兰语、`en` 发英语。

请从项目目录运行命令。若必须从其他目录调用，可设置
`AIRCO_TRACKER_HOME=~/airco-tracking`。

### 2. 验证

先检查网页解析，不发送邮件、不写入状态：

```bash
.venv/bin/airco-tracker check --dry-run --show-all
```

再测试邮件：

```bash
.venv/bin/airco-tracker send-test
```

检查后端配置和状态存储，但不发送邮件：

```bash
.venv/bin/airco-tracker doctor
```

最后正式运行一次：

```bash
.venv/bin/airco-tracker check
```

首次正式运行默认会把当前已有库存发给你。之后只通知新库存。若不想收到首次库存，在 `.env` 里设置 `ALERT_ON_FIRST_SEEN=false`。

### 3. macOS 后台自动运行

```bash
./install-launch-agent.sh
```

它会通过 macOS LaunchAgent 每 10 分钟检查一次，登录后自动恢复。查看日志：

```bash
tail -f ~/airco-tracking/tracker.log ~/airco-tracking/tracker.err.log
```

停止后台任务：

```bash
./uninstall-launch-agent.sh
```

## 部署到 Azure

前置条件：

- 有效的 Azure Subscription。
- Azure CLI，并已执行 `az login`。
- 当前账号可创建资源组、角色分配和相关 Azure 资源。

部署命令：

```bash
cd ~/airco-tracking
EMAIL_TO=you@example.com ./scripts/deploy-azure.sh
```

脚本会：

1. 创建 ACR、Blob Storage、Key Vault、Container Apps Environment、Managed Identity 和 Communication Services Email。
2. 使用 ACR 云端构建镜像，本机不需要 Docker。
3. 创建每 10 分钟运行一次的 Container Apps Job。
4. 立即启动一次手动执行，便于检查抓取和邮件送达。

Azure RBAC 新角色偶尔需要几分钟传播。如果第一次执行出现 ACR、Blob 或 Communication Services 的 403，请稍等后重新运行：

```bash
az containerapp job start --name airco-tracker-job --resource-group airco-tracker-rg
```

查看执行和日志：

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

定时表达式使用 UTC。`*/10 * * * *` 每 10 分钟执行一次，不受夏令时影响。

### 本地构建容器（可选）

如果本机已安装 Docker：

```bash
./scripts/test-container.sh
```

`.dockerignore` 明确排除了 `.env`、状态和日志，任何本地密码都不会进入镜像。

### Key Vault 配置加载

生产收件地址保存在 Key Vault，不进入源码或 GitHub Variables。已有部署可运行以下脚本迁移或更换地址：

```bash
./scripts/configure-notification-email.sh
```

容器通过以下映射和 Managed Identity 读取：

```text
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net
KEY_VAULT_SECRET_MAP=EMAIL_TO=notification-email
```

程序通过 Managed Identity 读取，secret 不进入代码、镜像或 Bicep 参数。

## GitHub Actions CI/CD

仓库已为 `ProgrammerAsahi/airco-tracking` 配置两条流水线：

- `.github/workflows/ci.yml`：Pull Request 执行 Python、Shell 和 Bicep 验证。
- `.github/workflows/deploy.yml`：`main` 推送通过测试后，用 commit SHA 构建不可变镜像并更新 Azure Job。

Azure 登录使用 GitHub OIDC 短期令牌，不创建 Client Secret。联邦身份只信任该仓库的 `main` 分支，并通过 `Airco GitHub Deployer Minimal` 自定义角色获得部署所需的最小权限；它没有目标资源组 Contributor 权限，不能创建角色分配，也不会读取应用的 Key Vault secrets。

### 首次引导顺序

先在本地完成 Azure 基础设施和 OIDC 信任，最后再首次推送 `main`，避免工作流因变量尚未配置而失败：

```bash
brew install azure-cli gh
az login
gh auth login

cd ~/airco-tracking
EMAIL_TO=you@example.com ./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

若 `gh` 未安装或未登录，引导脚本会打印以下六个值，请在 GitHub 仓库的 **Settings → Secrets and variables → Actions → Variables** 中手动建立：

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP
EMAIL_LANG
KEY_VAULT_SECRET_MAP
```

这些都是标识符或普通配置，不是密码。不要创建或上传 `AZURE_CREDENTIALS`、Client Secret、Subscription Access Token。

### 首次推送

如果 GitHub 仓库为空：

```bash
cd ~/airco-tracking
git init -b main
git remote add origin https://github.com/ProgrammerAsahi/airco-tracking.git
git add .
git commit -m "Initial airco tracker with Azure CI/CD"
git push -u origin main
```

`.env`、`.venv`、状态和日志均已被 `.gitignore` 排除。之后每次合并或推送到 `main` 都会部署一次；镜像使用完整 Git commit SHA，不覆盖 `latest`。

## 筛选条件

在 `.env` 中可设置：

- `MAX_PRICE_EUR=1500`：只通知 1,500 欧元以内的商品。价格暂时无法识别的商品仍会通知，避免漏报。
- `MIN_BTU=7000`：低于 7000 BTU 的商品不通知。无法从列表页识别 BTU 的正规空调仍会保留，避免漏报。

## 实时库存快照

每次正式检查都会生成独立的 `inventory.json`，按网站保存当前所有可在线购买的便携式压缩机空调。快照不应用价格、BTU 或品牌提醒过滤；aircooler、风扇、配件、固定式分体空调等适配器级排除规则仍然有效。邮件仍只针对首次出现或恢复库存且通过上述提醒过滤的商品发送。

成功检查的网站会完整替换自己的库存（包括清空为 0）；检查失败的网站保留上次成功库存，并标记为 `stale: true` 和 `status: error`。本地模式写入项目目录的 `inventory.json`，Azure 写入现有 `airco-tracker` Blob 容器的 `inventory.json`，无需新增云资源。`--dry-run` 不写快照或提醒状态。

## 维护与扩站

每个网站位于 `airco_tracker/adapters/<country>/` 的独立适配器中。新增网站时继承 `Adapter`，在该国家包的 `ADAPTERS` 列表和 `adapters/registry.py` 中注册，并为站点维护保守的 `delivery_coverage` 配送覆盖（ISO-2 国家码或 `eu`/`eea`/`nordics`/`benelux`/`dach` 区域别名）。网页结构改变会在日志中报出“parser found no products”，不会静默假装成功。

请保持 10 分钟或更长的检查间隔。库存和配送信息最终以商品页面为准。
