# Airco Tracker NL

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/README-简体中文-d73a49"></a>
  <a href="./README.en.md"><img alt="English" src="https://img.shields.io/badge/README-English-0969da"></a>
  <a href="./README.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/README-Nederlands-f58220"></a>
</p>

一个轻量的荷兰便携空调库存追踪器，支持本地运行和无密码 Azure 部署。当前监控：

- Coolblue
- MediaMarkt NL
- bol.com（通过官方 Marketing Catalog API；需要 Affiliate API 凭据）

它只在商品首次被发现为可购买，或从缺货变为有货时发送邮件；不会每 10 分钟轰炸邮箱。单个零售商失效时，其余站点仍会继续检查。

bol.com 的网页搜索路径不再抓取：Azure 数据中心 IP 会收到 403，而且 bol 的 robots.txt 明确限制该搜索路径。未配置官方 API 凭据时，bol 适配器会明确保持 disabled，Coolblue 和 MediaMarkt 不受影响。

## Azure 架构

生产环境采用：

```text
Container Apps Scheduled Job
  ├─ Managed Identity → Blob Storage（库存状态）
  ├─ Managed Identity → Communication Services Email（通知）
  └─ Managed Identity → Key Vault（可选第三方密钥）
```

Azure 模式不保存邮箱密码、Storage Key、Communication Services Key 或 ACR 密码。收件地址、BTU 和价格限制不是秘密，作为普通环境配置传入。Key Vault 只为未来无法消除的第三方密钥预留。

### 启用 bol.com 官方 API

bol 的 Marketing Catalog API 提供官方商品搜索、NL 最佳报价、价格和配送描述。先申请 bol Affiliate Program，并在 Affiliate Portal 的 Open API 区域创建 Client ID 和 Client Secret。不要把凭据粘贴到代码、GitHub Variables 或聊天中。

代码部署后，在本机运行：

```bash
./scripts/configure-bol-api.sh
```

脚本会隐藏输入 Client Secret，将两项凭据写入 Azure Key Vault，设置不敏感的 GitHub Actions 变量，并触发一次部署。容器运行时再通过 Managed Identity 读取秘密。

## 本地运行

### 1. 安装

```bash
cd ~/airco-tracking-nl
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
```

编辑 `.env`，填入收件邮箱和 SMTP。Gmail 用户需要开启两步验证，并创建一个“应用专用密码”；不要填写日常登录密码。

请从项目目录运行命令。若必须从其他目录调用，可设置
`AIRCO_TRACKER_HOME=~/airco-tracking-nl`。

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
tail -f ~/airco-tracking-nl/tracker.log ~/airco-tracking-nl/tracker.err.log
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
cd ~/airco-tracking-nl
EMAIL_TO=asahi.lee.eu@outlook.com ./scripts/deploy-azure.sh
```

脚本会：

1. 创建 ACR、Blob Storage、Key Vault、Container Apps Environment、Managed Identity 和 Communication Services Email。
2. 使用 ACR 云端构建镜像，本机不需要 Docker。
3. 创建每 10 分钟运行一次的 Container Apps Job。
4. 立即启动一次手动执行，便于检查抓取和邮件送达。

Azure RBAC 新角色偶尔需要几分钟传播。如果第一次执行出现 ACR、Blob 或 Communication Services 的 403，请稍等后重新运行：

```bash
az containerapp job start --name airco-tracker-job --resource-group airco-tracker-nl-rg
```

查看执行和日志：

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

定时表达式使用 UTC。`*/10 * * * *` 每 10 分钟执行一次，不受夏令时影响。

### 本地构建容器（可选）

如果本机已安装 Docker：

```bash
./scripts/test-container.sh
```

`.dockerignore` 明确排除了 `.env`、状态和日志，任何本地密码都不会进入镜像。

### 可选 Key Vault 密钥加载

Azure 默认模式完全无密码，因此 Key Vault 初始为空。若将来某个网站需要 API Key，可在 Key Vault 建立 secret，并设置：

```text
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net
KEY_VAULT_SECRET_MAP=PARTNER_API_KEY=partner-api-key
```

程序通过 Managed Identity 读取，secret 不进入代码、镜像或 Bicep 参数。

## GitHub Actions CI/CD

仓库已为 `ProgrammerAsahi/airco-tracking-nl` 配置两条流水线：

- `.github/workflows/ci.yml`：Pull Request 执行 Python、Shell 和 Bicep 验证。
- `.github/workflows/deploy.yml`：`main` 推送通过测试后，用 commit SHA 构建不可变镜像并更新 Azure Job。

Azure 登录使用 GitHub OIDC 短期令牌，不创建 Client Secret。联邦身份只信任该仓库的 `main` 分支，并且只拥有目标资源组的 Contributor 权限；它不能创建角色分配，也不会读取应用的 Key Vault secrets。

### 首次引导顺序

先在本地完成 Azure 基础设施和 OIDC 信任，最后再首次推送 `main`，避免工作流因变量尚未配置而失败：

```bash
brew install azure-cli gh
az login
gh auth login

cd ~/airco-tracking-nl
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

若 `gh` 未安装或未登录，引导脚本会打印以下五个值，请在 GitHub 仓库的 **Settings → Secrets and variables → Actions → Variables** 中手动建立：

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP
EMAIL_TO
```

这些都是标识符或普通配置，不是密码。不要创建或上传 `AZURE_CREDENTIALS`、Client Secret、Subscription Access Token。

### 首次推送

如果 GitHub 仓库为空：

```bash
cd ~/airco-tracking-nl
git init -b main
git remote add origin https://github.com/ProgrammerAsahi/airco-tracking-nl.git
git add .
git commit -m "Initial airco tracker with Azure CI/CD"
git push -u origin main
```

`.env`、`.venv`、状态和日志均已被 `.gitignore` 排除。之后每次合并或推送到 `main` 都会部署一次；镜像使用完整 Git commit SHA，不覆盖 `latest`。

## 筛选条件

在 `.env` 中可设置：

- `MAX_PRICE_EUR=1500`：只通知 1,500 欧元以内的商品。价格暂时无法识别的商品仍会通知，避免漏报。
- `MIN_BTU=7000`：低于 7000 BTU 的商品不通知。无法从列表页识别 BTU 的正规空调仍会保留，避免漏报。

## 维护与扩站

每个网站位于 `airco_tracker/adapters/` 的独立适配器中。新增网站时继承 `Adapter` 并在 `cli.py` 注册即可。网页结构改变会在日志中报出“parser found no products”，不会静默假装成功。

请保持 10 分钟或更长的检查间隔。库存和配送信息最终以商品页面为准。
