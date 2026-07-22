# Airco Tracker

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
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
- EcoFlow France
- E.Leclerc France
- Maison Energy
- Create France
- Evolarshop France
- Klarstein France
- Trotec France
- De’Longhi France
- Lidl France
- Action France
- H2R Équipements
- Obelink France
- Narbonne Accessoires
- Mon Camping Car

Boulanger 和 Brico Dépôt France 的适配器代码已保留，但暂未在生产注册：Boulanger 页面本地可访问，而 Azure Container Apps 出站请求会稳定卡到 60 秒读取超时；Brico Dépôt 的普通分类页和 smartcache fragment 本地可读，但 Azure 出站请求会返回过小/不可用响应。两者都需要找到稳定的页面内 API 或官方/公开替代源后再启用。

它只在商品首次被发现为可购买，或从缺货变为有货时发送邮件；不会每 10 分钟轰炸邮箱。单个零售商失效时，其余站点仍会继续检查。

法国站点同样区分即时现货和预售：`Pré-commande`、`Expédition à partir`、`livraison prévue semaine`、`Sur commande` 和多周交期会显示为预售库存，但不会触发现货邮件。Electro Dépôt France 读取页面内 Vue JSON 的 `stock` 数字；EcoFlow France 读取第一方 Shopify collection 与商品页，以 variant 的 `available` 标志优先于营销预订文案，并使用官网确认的 WAVE 2/3（5100/6100 BTU）容量。

E.Leclerc France 不抓取其反爬店面页面，而是通过店面使用的第一方同源前端 API 工作：约每 12 小时用商品搜索低频发现候选，已知 SKU 则每 10 分钟通过批量详情刷新库存。商品必须精确满足 `family.code=climatiseur`，配件、冷风机和风扇会被排除。offer 的 `additionalFields` 中 `availability-status` 是权威状态：即时现货必须同时满足 `in-stock`、数字库存大于 0、处于有效日期窗口、EUR 价格有效且大于 0、seller 非空；预售只接受 `preorder`、`unlimited-preorder`、`forthcoming` 或 `future-stock`，并同样要求有效日期窗口、有效价格和 seller，但库存可以为 0。`shipped-under`、`temporarily-unavailable`、`unavailable`、缺失/未知状态，以及库存不大于 0 的 `in-stock` 均按不可用处理；多个 offer 优先选择最便宜的即时现货，其次选择最便宜的预售，否则为不可用。库存真相来自这套第一方 live API，用户跳转链接才使用已批准的 Awin deep link；当前 E.Leclerc Awin Product Feed 不包含空调，因此运行时不依赖 Awin Product Feed data key。

Trotec France 每轮扫描都读取其官方店面使用的 Algolia 商品索引；它是即时库存和预售分类的唯一权威来源。即使 `availability_status` 显示 `En stock`，只要 `sold_out=Oui` 就会被硬性否决。已批准的 Awin advertiser `62319` 只用于 Link Builder：系统把已验证的 `fr.trotec.com/shop/...` 商品 URL 以最多 100 条一批提交给官方 API，并把成功结果缓存一天。只有 API 返回的链接通过 HTTPS、Awin host、advertiser、publisher 和最终 Trotec URL 校验后才写入 `affiliate_url`；API 超时、单条拒绝或无 token 时直接使用 Trotec canonical URL，绝不会让库存扫描失败或变成 stale。`Product.url` 始终作为库存状态、去重和变化事件的稳定身份；网页和提醒邮件会在推广链接前显示披露。

AliExpress 已实现共用的 Affiliate Open Platform 客户端，以及分别绑定法国、荷兰配送目的地的检查适配器。每次搜索和 SKU 详情请求都会携带真实配送国家；法国返回结果绝不会被复用为“可配送荷兰”的证据，反之亦然。已获批的 SKU 接口契约只提供商品、含税价格、运费、发货国、预计交期和 SKU 属性，并没有文档化的库存量或可下单字段。因此 AliExpress 目前只用于诊断，刻意没有加入生产 adapter registry：返回 SKU、价格、交期或推广链接都不会被当成现货。详见 [docs/ALIEXPRESS_INTEGRATION.zh.md](docs/ALIEXPRESS_INTEGRATION.zh.md)。

Maison Energy 会让 `Non disponible`/`Demande de devis` 优先于 schema `PreOrder`，避免不可下单商品触发现货。H2R Équipements 只读取房车/露营车的 `climatisation nomade` 分类，`En stock` 才算即时现货，`Sur commande`/`Retour en stock prévu` 不会触发现货提醒；Obelink France 通过公开 JSON-LD 分类和商品页追踪 mobile/split 露营空调；Narbonne Accessoires 只在 `Livraison à Domicile` 明确 `En stock` 时算可配送，避免把仅门店自取误报为现货；Mon Camping Car 的 `BackOrder`/`Disponible à partir` 会作为预售展示。Costway France 因生产环境持续返回 HTTP 403，已从活跃注册表移除并保留为 deferred adapter；Boulanger、Brico Dépôt France、Cdiscount 以及其它直接 403 的法国站点也暂未启用，避免把超时、反爬页或 JS 壳误当库存来源。Action France 当前搜索结果主要是冷风机/风扇，因此会被严格过滤。

EP.nl 通过服务器输出的商品卡识别在线库存；Electro World 使用其网页公开调用的只读商品搜索索引，并在每次运行时动态读取公开搜索配置；Wehkamp 读取分类页的主商品数据。三者均不需要账号或秘密凭据。Wehkamp 会把售罄商品从分类移除，因此明确的空分类是正常状态；商品补货并重新出现时会立即触发首次有货提醒。

Lidl 不抓取 robots.txt 禁止的搜索路径，而是通过其公开商品 sitemap 发现真正的移动空调，再读取商品页的 JSON-LD 库存。GAMMA 和 KARWEI 正常情况下共用服务器商品卡解析器；分类站点对 Azure 限流时，适配器改用店面公开的只读目录查询，并要求线上可购、临时缺货、数量、库存和 availability 字段相互一致。它们在 robots.txt 中声明的商品 sitemap 只作为“当前目录没有移动空调候选”的安全兜底，绝不会仅凭 sitemap 收录就判定现货。仅门店库存或仅自取不会提醒。Praxis 同时检查当前可用性和送货方式，只有支持荷兰地址配送的商品才会提醒，并排除 split airco、aircooler 和配件。

Alternate.nl、FlinQ 和 Action Webshop 通过 robots.txt 中公布的商品 sitemap 自动发现新型号，再读取商品页库存；Action 还持续检查已知的过期季节商品，以便原链接重新上架时立即发现。Trotec 和 Klarstein 读取服务器输出的分类商品数据。Trotec 的数周交期、预售或“可加入购物车”不会被误判成即时现货，只有明确的 `Op voorraad` 才会提醒；Klarstein 只接受其明确的在线库存字段。五家网站均会排除 aircooler、风扇和空调配件。

Expert 只有明确可在线下单时才算有货，仅门店库存不会提醒；De’Longhi 读取商品页官方 JSON-LD，`Breng mij op de hoogte` 视为缺货；Obelink 和 Kampeerwereld 会持续检查已知季节商品，即使它们暂时从分类页消失；Create 的 `Presale` 和 `Verzending vanaf` 都不会触发提醒。

Costway NL 读取 Magento 分类页的 `qty-N` 库存数量；Evolarshop 通过其公开的 Nosto 搜索 API 获取商品，并排除"无排气管"（zonder afvoerslang）的非压缩机产品；Airco voor in huis 使用 WooCommerce 的 `instock`/`outofstock` 状态；Solago 读取 Shopify JSON-LD，`Voorbestelling` 和 `Levering vanaf` 预售文本会覆盖 InStock 标记为缺货。Hubo 没有 airco 分类页，通过 Shopify 商品 sitemap 发现便携空调并读取 JSON-LD 库存；Vrijbuiter 追踪露营和房车用便携式分体空调（如 Mestic SPA、Qlima MS-AC），排除 aircooler 和配件。Klimaatshop 是专业空调商，从 `data-url` 属性提取产品、`.stock` span 判断库存；Airco-Webwinkel 通过 sitemap 发现产品并读取 WooCommerce JSON-LD 库存。

Bostools 同时读取 WooCommerce 的移动空调和房车空调分类。`Leverbaar vanaf: 日期` 作为预售显示在网页但不发邮件；明确售罄、仅自取、无包装展示品和配件不会触发提醒。价格只读取面向消费者的含税价，不会误取旁边的 `excl. btw` 企业价。

Conrad.nl 暂未启用：普通网页从 Azure 和本地请求都会收到 Cloudflare 403。Conrad 官方 Developer Portal 提供 Price & Availability API，但需要单独申请访问；本项目不会绕过其反爬保护。

## Azure 架构

生产提醒已经从“扫描任务直接逐个发邮件”拆成异步流水线：

```text
Scanner Job（每 10 分钟）
  └─ Table outbox → Service Bus topic
       └─ 32 个 recipient shard jobs
            └─ 每位有效订阅用户一个 email job
                 └─ 幂等 delivery ledger → ACS Email
```

Scanner 只负责抓取、更新私有 `state.json`/`inventory.json` 并持久化确定性的库存事件，不会枚举用户或等待邮件发送。Container Apps workers 通过 Azure Service Bus Standard 独立扩缩容；用户投影按 32 个 Azure Table partitions 分片并流式读取，因此 subscriber 数量不会拖慢库存扫描。

Azure 设计通过按职责分离的 Managed Identities 承担 Web、scanner、后端 retention、Web 认证数据 retention、outbox publisher、fan-out 和 email delivery；新流水线权限尽量限制到具体 container/entity/table。Service Bus 消息只保存匿名 user UUID，不保存邮箱、昵称、Stripe/payment 或卡片数据；Email worker 在发送前重新读取最新邮箱、语言、配送国家和订阅权益。专用 Azure Table 会跨 replicas 预留 ACS 发送时隙；本地开发使用进程内 limiter，而禁用 distributed backend 时 Bicep 会把 replicas 强制限制为一个。`EMAIL_TO` 只属于本地 direct/SMTP mode，不是生产 subscriber 数据源。

Azure Monitor 已启用四条 Service Bus metric alerts，分别监控 dead-letter messages、持续 backlog、throttled requests 和 server errors。生产通过 `aircontrack-operations-alerts` Action Group 通知运维。Receiver 地址只作为 secure foundation parameter `operationsAlertEmail` 传入，通常由本地 `AZURE_OPERATIONS_ALERT_EMAIL` 提供；它不会提交到仓库或保存为 GitHub Actions variable。重复部署 foundation 时，即使没有再次提供该环境变量，脚本也会保留 Action Group 现有 receiver。

完整拓扑、幂等语义、配置、数据保留、扩容限制、部署顺序和运维命令见 [异步提醒流水线](./docs/ALERT_PIPELINE.zh.md)；自定义 ACS 发件域名的 DNS、验证、切换、真实投递和回滚流程见 [ACS 自定义邮件域名运维手册](./docs/ACS_CUSTOM_EMAIL_DOMAIN.zh.md)。

## 本地运行

### 1. 安装

```bash
cd ~/airco-tracking
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

编辑 `.env`，填入收件邮箱和 SMTP。Gmail 用户需要开启两步验证，并创建一个“应用专用密码”；不要填写日常登录密码。

可选设置 `EMAIL_LANG`（默认 `zh`）：`zh` 发中文、`nl` 发荷兰语、`en` 发英语、`fr` 发法语。生产提醒会以用户 Profile 保存的语言为准，并按收件人的配送国家生成正文和金额格式。

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

两个仓库共享的 foundation/RBAC 变更必须按以下顺序部署：

```bash
cd ~/airco-tracking
AZURE_FOUNDATION_ONLY=true ./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh

cd ~/airco-tracking-web
./scripts/deploy.sh

cd ~/airco-tracking
./scripts/deploy-application.sh
./scripts/migrate-runtime-identities.sh
# 两个仓库 smoke tests 均通过后才执行：
./scripts/migrate-runtime-identities.sh --apply
```

脚本会：

1. 注册所需 Azure resource providers，创建 ACR、Storage、Key Vault、Container Apps Environment、Service Bus Standard、ACS Email 和按职责分离的 Managed Identities。
2. 创建 alert outbox/recipient/delivery Tables 和专用 `emailratelimit` Table，topic/subscription、两条 queues、四条 Service Bus metric alerts 和已配置的运维 Action Group。
3. Foundation-only 模式不会移动 workload，方便 Web 仓库先用新 identity 部署并验证 cleanup job。
4. Web 和后端 application scripts 会在 ACR 构建 immutable images、部署 workloads，并在完成前进行验证。已有后端环境会先用候选镜像运行一次 reconciler canary，失败时不会改动生产定义。
5. 最后的 identity migration 先执行只读审计；只有两个仓库 smoke tests 均通过后才显式使用 `--apply` 删除已验证的旧 grants。真实邮件使用定向的 `pipeline-test` 另行验证，部署脚本不会给全体用户发测试邮件。

Foundation 会创建 RBAC，必须由能创建 role assignments 的本地 Azure principal 运行。运行身份的 Key Vault 权限限制到具体 secret：Web 仅 unsubscribe/withdrawal/OTP pepper，scanner 仅 Awin/AliExpress，email worker 仅 unsubscribe；Web 清理 Job 使用专属 `${prefix}-web-retention` identity，它只能拉取镜像并写 `users`/`authcodes`/`authsessions`，后端 retention identity 无权访问这三张认证表。GitHub deployer 故意没有 role-assignment 权限，只部署 application layer。Azure RBAC 新角色偶尔需要几分钟传播；首次出现 ACR、Storage、Service Bus 或 ACS 403 时，请等待后重跑 application deployment：

```bash
AZURE_RESOURCE_GROUP=airco-tracker-rg ./scripts/deploy-application.sh
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

正式 fan-out 收件人来自 Web 项目同步维护的 `alertrecipients` Table。用户在 Profile 修改邮箱后会保留稳定 UUID，而 email worker 会在每次发送前按 UUID 点读 canonical 用户，因此投影短暂落后也不会发往旧地址。生产不会读取 `notification-email` 或 `EMAIL_TO` 作为回退；无法核对 canonical 账户时必须 fail closed。

Key Vault 只保留必要的第三方 adapter 秘密凭据。容器可通过 Managed Identity 加载 Trotec France Awin Link Builder 的 publisher token：

```text
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net
KEY_VAULT_SECRET_MAP=AWIN_PUBLISHER_API_TOKEN=awin-publisher-api-token,ALIEXPRESS_APP_KEY=aliexpress-app-key,ALIEXPRESS_APP_SECRET=aliexpress-app-secret
```

系统只调用 Awin Link Builder Batch API，Bearer token 仅进入 `Authorization` header；不支持把 data-feed key 放进 URL 的 Legacy Create-a-Feed 路径。扫描先完成第一方库存判定，再批量生成并校验推广链接；API 失败时回退 canonical URL。尚未接入 CMP，因此 API 返回的链接会被明确覆盖为 `cons=0`：Awin 不设置 cookie，也不向商家传递 click identifier，当前不能依赖其完成佣金归因。网页和邮件都会在点击前显示推广联盟披露；以后只有在实现真实同意管理后才能按当次选择发送 `cons=1`。

Secret 不进入代码、镜像、Bicep 参数或 Service Bus 消息。更换订阅邮箱必须通过 Web Profile，不能通过 Key Vault 或部署变量修改。

## GitHub Actions CI/CD

仓库已为 `ProgrammerAsahi/airco-tracking` 配置两条流水线：

- `.github/workflows/ci.yml`：Pull Request 执行 Python、Shell 和 Bicep 验证。
- `.github/workflows/deploy.yml`：符合触发条件的 `main` 推送通过测试后，用 commit SHA 构建不可变镜像，并更新 scanner/publisher/reconciler/retention jobs 以及 fan-out/email worker apps。

Azure 登录使用 GitHub OIDC 短期令牌，不创建 Client Secret。联邦身份只信任该仓库的 `main` 分支，并通过 `Airco GitHub Deployer Minimal` 自定义角色获得部署所需的最小权限；它没有目标资源组 Contributor 权限，不能创建角色分配，也不会读取应用的 Key Vault secrets。

### 首次引导顺序

先在本地完成 Azure 基础设施和 OIDC 信任，最后再首次推送 `main`，避免工作流因变量尚未配置而失败：

```bash
brew install azure-cli gh
az login
gh auth login

cd ~/airco-tracking
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

若 `gh` 未安装或未登录，引导脚本会打印以下十个值，请在 GitHub 仓库的 **Settings → Secrets and variables → Actions → Variables** 中手动建立：

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

这些都是标识符或普通配置，不是密码。`DEPLOYMENT_PAUSED` 通常为 `false`；维护窗口临时设为 `true` 时，部署会保持 scanner/publisher 暂停且不会主动启动它们做验证。不要创建或上传 `AZURE_CREDENTIALS`、Client Secret、Subscription Access Token。

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

`.env`、`.venv`、状态和日志均已被 `.gitignore` 排除。之后每次符合条件的非文档 `main` 推送都会部署一次；纯文档和仅修改部署 workflow 的 push 会被忽略，必要时可使用 `workflow_dispatch` 发布。镜像使用完整 Git commit SHA，不覆盖 `latest`。

## 筛选条件

在 `.env` 中可设置：

- `MAX_PRICE_EUR=1500`：只通知 1,500 欧元以内的商品。价格暂时无法识别的商品仍会通知，避免漏报。
- `MIN_BTU=7000`：低于 7000 BTU 的商品不通知。无法从列表页识别 BTU 的正规空调仍会保留，避免漏报。

## 实时库存快照

每次正式检查都会生成独立的 `inventory.json`，按网站保存当前所有可在线购买的便携式压缩机空调。快照不应用价格、BTU 或品牌提醒过滤；aircooler、风扇、配件、固定式分体空调等适配器级排除规则仍然有效。邮件仍只针对首次出现或恢复库存且通过上述提醒过滤的商品发送。

成功检查的网站会完整替换自己的库存（包括清空为 0）；检查失败的网站只把上次成功库存短期保留为诊断上下文，并标记为 `stale: true`、`status: error` 和 `counts_toward_totals: false`，stale 商品绝不会增加实时汇总。超过 24 小时诊断截止时间（或不存在可信的成功时间）后，生产端会清空旧商品列表，同时保留站点健康元数据。新增的 additive freshness 字段会提供已验证站点数、confidence、stale age 和该诊断截止时间，同时保持 schema version `1`。本地模式写入项目目录的 `inventory.json`，Azure 写入现有 `airco-tracker` Blob 容器的 `inventory.json`，无需新增云资源。`--dry-run` 不写快照或提醒状态。

## 维护与扩站

每个网站位于 `airco_tracker/adapters/<country>/` 的独立适配器中。新增网站时继承 `Adapter`，在该国家包的 `ADAPTERS` 列表和 `adapters/registry.py` 中注册，维护保守的 `delivery_coverage`，并把 canonical hosts 加入 `MERCHANT_HOSTS_BY_SITE_ID`；未知商家或 affiliate host 会 fail closed。完整的安全、数据保留、pending index 和 Owner-only 身份迁移流程见[运行时加固与迁移](./docs/HARDENING.zh.md)。网页结构改变会在日志中报出“parser found no products”，不会静默假装成功。

请保持 10 分钟或更长的检查间隔。库存和配送信息最终以商品页面为准。

## 文档语言维护

所有 Markdown 文档都应提供中文和英语版本，并在顶部提供语言切换 badge。以后修改任何文档时，必须同步更新两个语言版本。
