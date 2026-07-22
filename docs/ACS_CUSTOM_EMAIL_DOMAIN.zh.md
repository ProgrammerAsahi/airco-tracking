# ACS 自定义邮件发件域运维手册

<p align="center">
  <a href="./ACS_CUSTOM_EMAIL_DOMAIN.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/ACS_DOMAIN-简体中文-d73a49"></a>
  <a href="./ACS_CUSTOM_EMAIL_DOMAIN.md"><img alt="English" src="https://img.shields.io/badge/ACS_DOMAIN-English-0969da"></a>
</p>

最后检查：2026-07-22

必须同时更新本文件和 `ACS_CUSTOM_EMAIL_DOMAIN.md`。两个文件都不得记录联系邮箱、recipient UUID、access token、subscription ID、支持工单附件或其他个人信息。

本手册用于在保留 Azure-managed fallback 的前提下，把生产邮件从低吞吐的 Azure-managed sender 切换到 customer-managed `airco-tracker.eu`。Azure resource 的创建、验证和连接可以通过 Azure CLI 完成；DNS 仍需在权威 DNS 提供商（目前是 Dynadot）中发布，或通过另行授权的 Dynadot API 修改。

当前生产状态（2026-07-22）：Domain/SPF/DKIM/DKIM2 均为 `Verified`，自定义域与 `AzureManagedDomain` 继续同时保持连接，backend/frontend 都明确选择自定义域。Gmail/Outlook 真实 canary 均进入收件箱，原始邮件头显示 SPF、DKIM 与 DMARC 对齐并通过。DMARC 刻意保持观察策略（`p=none`）。受监控的 `support` Reply-To 和 `dmarc` aggregate-report aliases、它们的入站 MX 路由、recipient-level final-delivery ingestion 与 hard-bounce suppression 均已上线。一次 ACS 发送已通过生产 Event Grid 路径从 `accepted` 推进到 `delivered`。Higher-quota support request 已提交但仍在等待审批，因此必须继续保持保守限速。

## 安全规则

- 在 Domain、SPF、DKIM 和 DKIM2 全部显示 `Verified` 且域名已经连接到 Communication Service 之前，绝不能让应用选择自定义域。
- 发布和回滚期间都保留 `AzureManagedDomain` 的连接。连接自定义域时不得替换 fallback。
- 第一轮自定义域 canary 仍保持 `EMAIL_MIN_SEND_INTERVAL_SECONDS=13`、`EMAIL_MAX_REPLICAS=1`。只有确认适用 quota 后才能提高吞吐。
- 不得通过删除/重建 domain resource 来重试验证。新资源可能生成新的 ownership token，使本文中的 DNS 值失效。
- 不得发布第二条 SPF policy。一个域名只能有一条 `v=spf1` TXT；未来增加其他发件服务时，应合并进现有值。
- 不得把收件地址写入命令、日志、仓库中的 support 文档或 Service Bus message。生产 canary 只能使用预先授权的匿名 recipient UUID。

## 当前 Azure resources

Helper 会在 `airco-tracker-rg` 中自动发现 Email Communication Service 和 Communication Service，通常不需要复制 resource name 或 ID：

```bash
cd ~/airco-tracking
az login
./scripts/configure-acs-custom-domain.sh status
./scripts/configure-acs-custom-domain.sh records
```

Customer-managed `airco-tracker.eu` domain resource 已经存在。`prepare` 是幂等命令，只用于资源缺失的情况：

```bash
./scripts/configure-acs-custom-domain.sh prepare
```

`records` 的输出才是权威值。如果以后有意替换 domain resource，必须使用新资源生成的值，不能复用下面的历史值。

## `airco-tracker.eu` DNS 记录

当前 ACS domain resource 生成了以下四条记录：

| 用途 | 类型 | Dynadot host | 值 | TTL |
| --- | --- | --- | --- | ---: |
| Domain ownership | TXT | apex / `@` | `ms-domain-verification=5bb3aa08-d101-40e2-aca9-c2c3eb18f493` | 3600 |
| SPF | TXT | apex / `@` | `v=spf1 include:spf.protection.outlook.com -all` | 3600 |
| DKIM | CNAME | `selector1-azurecomm-prod-net._domainkey` | `selector1-azurecomm-prod-net._domainkey.azurecomm.net` | 3600 |
| DKIM2 | CNAME | `selector2-azurecomm-prod-net._domainkey` | `selector2-azurecomm-prod-net._domainkey.azurecomm.net` | 3600 |

Dynadot 可能把根记录称为 domain record、空 host 或 `@`。在 subdomain 输入框只填相对 DKIM host；如果 UI 会自动附加 zone，就不能再手工加上 `airco-tracker.eu`。TXT value 不要额外加引号。

Apex 上原有的 Container Apps ownership TXT 与邮件无关，必须保留。Apex 可以有多条普通 TXT，但不能有多条 SPF policy。

在请求 ACS 验证前，先确认公共 DNS 已解析：

```bash
dig +short TXT airco-tracker.eu
dig +short CNAME selector1-azurecomm-prod-net._domainkey.airco-tracker.eu
dig +short CNAME selector2-azurecomm-prod-net._domainkey.airco-tracker.eu
```

## 验证和连接

四条记录都能从公共 DNS 读取后，发起四项必需验证：

```bash
./scripts/configure-acs-custom-domain.sh verify
./scripts/configure-acs-custom-domain.sh status
```

DNS 传播和 ACS polling 都是异步的。可以重复运行 `status`，但不要反复重建 DNS 或 Azure domain。必需的成功条件是：

```text
Domain = Verified
SPF    = Verified
DKIM   = Verified
DKIM2  = Verified
```

DMARC 值得配置，但 ACS 当前不会为本域返回生成的 DMARC verification value。ACS 中的状态可能一直是 `NotStarted`；收件系统会独立读取并执行公开的 DMARC policy。Helper 不把它作为四项必需 link gate 之一。

只有四项必需状态全部 verified 后才能连接：

```bash
./scripts/configure-acs-custom-domain.sh link
./scripts/configure-acs-custom-domain.sh status
```

验证不完整时 `link` 会 fail closed，并且连接时会同时保留 custom domain 和 `AzureManagedDomain`。`infra/foundation.bicep` 接受 `customEmailDomainId`；以后再次运行 `scripts/deploy-azure.sh` 时，会读取并保留已经连接的 custom domain。

## 在 application deployment 中选择域名

后端库存提醒和前端登录验证码都会使用 ACS。两个部署 workflow 都读取 GitHub Actions variable `ACS_EMAIL_DOMAIN_NAME`；只有验证和连接完成后才能同时设置两个仓库：

```bash
./scripts/configure-acs-custom-domain.sh configure-github
gh workflow run deploy.yml --repo ProgrammerAsahi/airco-tracking --ref main
gh workflow run deploy.yml --repo ProgrammerAsahi/airco-tracking-web --ref main
```

确认 workflow 成功，并确认 email worker 得到：

```text
ACS_EMAIL_DOMAIN_NAME=airco-tracker.eu
EMAIL_FROM=DoNotReply@airco-tracker.eu
```

不要为了这次切换把 `EMAIL_FROM`、domain ID、ACS credential 或 DNS-provider credential 保存成新的 GitHub secret。Deployment 会从 verified linked domain 派生默认 sender，并通过 Managed Identity 认证。

首次发布保持以下生产变量：

```text
ACS_EMAIL_DOMAIN_NAME=airco-tracker.eu
EMAIL_MIN_SEND_INTERVAL_SECONDS=13
EMAIL_MAX_REPLICAS=1
DEPLOYMENT_PAUSED=false
```

Helper 会同时更新两个仓库：后端 worker 发送库存提醒，前端服务发送登录验证码。移除或修改任何 fallback 配置前，必须确认两个部署都已完成验证。

## 真实投递验收

按照 [ALERT_PIPELINE.zh.md](./ALERT_PIPELINE.zh.md) 中现有的 targeted synthetic-event 流程执行。ACS send operation 被接受不等于发布完成。

对两个已经授权的 canary 账户逐项确认：

1. Targeted publisher execution 成功，命令和 event 中不出现地址。
2. 对应 delivery-ledger rows 先达到 `accepted`，再进入 `delivered` 等 recipient-level final state；email-worker log 显示 ACS accepted，并且没有 ACS `429`。
3. 两个 inbox 都实际收到邮件，同时检查 spam/junk。
4. 查看原始邮件头，确认用户可见 From 是 `airco-tracker.eu`、SPF 通过、DKIM 通过且签名域对齐、DMARC 通过，同时确认 Return-Path/MailFrom 与自定义域对齐。
5. Stock subscription 和全部三条 queues（`email-fanout-jobs`、`email-jobs` 与 `acs-email-delivery-events`）的 active、scheduled、transfer-dead-letter 和 dead-letter counts 全部回到零。
6. 只记录 inbox/spam 数量和结果，不记录收件地址。原始 transport header 仍可能正常显示 Microsoft infrastructure；目标是从可见 sender 和 envelope sender 中移除 Azure-generated domain，不是隐藏投递服务商。

新域名必须用真实、已同意接收的流量逐步 warm up。即使技术 quota 允许，突然放大发送量也可能损害 reputation。

最近一次生产 reconciliation 没有找到 active entitled alert recipient。在这种状态下，完整库存提醒 synthetic test 必须安全 skip，不能强行指向已过期、已注销或其他不再符合权益的账户。直接生产 sender/验证邮件 canary 和 accepted-to-delivered provider-report 链路仍可独立验证。只有在存在获授权的 active entitled recipient 时才重跑完整 targeted pipeline；没有这类用户并不代表 pipeline 故障。

## Sender identity、DMARC、MX 和 Reply-To

初始 verified sender 是 `DoNotReply@airco-tracker.eu`。更友好的长期 identity 是 `Airco Tracker <alerts@airco-tracker.eu>`。

Additional sender username 需要 higher-than-default custom-domain sending limit。Quota 批准后可创建：

```bash
az communication email domain sender-username create --resource-group airco-tracker-rg --email-service-name <discovered-email-service-name> --domain-name airco-tracker.eu --sender-username alerts --username alerts --display-name "Airco Tracker"
```

当前 GitHub workflow 没有单独暴露 branded sender-username variable。因此选择 `alerts@airco-tracker.eu` 仍需要经过 review 的 application/deployment 改动；不能手改正在运行的 Container App。

当前邮件域控制：

- `support@airco-tracker.eu` 是受监控的入站 alias，登录验证和库存提醒邮件都通过 ACS 结构化 Reply-To 字段设置它。
- `dmarc@airco-tracker.eu` 是独立受监控的 aggregate-report alias。公开入站 MX records 为两个 aliases 提供路由，不改变 ACS sender identity。
- DMARC 以观察模式发布：

  ```text
  Host:  _dmarc
  Type:  TXT
  Value: v=DMARC1; p=none; rua=mailto:dmarc@airco-tracker.eu; pct=100; adkim=s; aspf=s
  ```

- 继续检查 aggregate reports 和 authentication alignment，之后才可逐步改为 `p=quarantine`，最后改为 `p=reject`。盘点所有合法 sender 之前不得直接发布严格 reject policy。
- 当未来需要隔离网站/root mailbox 和 transactional mail 的 sender reputation 时，可以使用 `notify.airco-tracker.eu`。它需要独立的 ACS custom-domain resource 和新生成的 DNS records，绝不能复用 apex ownership token。

## 提高 quota

官方文档中的 custom-domain 默认发送限制是每分钟 30 封、每小时 100 封。更高限制只提供给 verified custom domain，并通过 Azure Support 审核，不是 Bicep property。支持工单必须如实提供项目/网站、ACS resource/domain、transactional use case、每分钟/小时/天峰值、地址来源、退订/退信治理、reputation、complaint 和 delivery-failure 监控。

Azure CLI 技术上可以创建 support ticket，但请求仍需要私有联系人资料、国家/时区、目标容量和完整问卷。这些内容不能进入仓库。除非 operator 在执行时提供这些值，否则通过 portal 引导提交更安全。选择 **Communication Services → Assistance with email → Quota Increase**，只申请真实测量需求所支持的容量，并采用更严格的失败率低于 1% 目标。

### 当前 quota 申请状态

初始申请的生产前置控制已上线：Event Grid recipient-level final delivery、应用层 hard-bounce suppression、独立的邮件提醒偏好、可见与 RFC 8058 one-click unsubscribe 路径、application Reply-To、privacy cleanup 与 delivery-failure monitoring。Higher-quota request 已提交，但仍在等待 Azure 审批。明确获批之前，必须保持 `EMAIL_MIN_SEND_INTERVAL_SECONDS=13` 与 `EMAIL_MAX_REPLICAS=1`，逐步 warm up 域名，并把 ACS acceptance 视为中间状态，不能当作已进入 inbox 的证明。

## 回滚

最快的回滚是让应用重新使用仍然连接的 Azure-managed domain，并恢复安全限速；必须在修改 custom-domain DNS 之前执行：

```bash
gh variable set ACS_EMAIL_DOMAIN_NAME --repo ProgrammerAsahi/airco-tracking --body AzureManagedDomain
gh variable set ACS_EMAIL_DOMAIN_NAME --repo ProgrammerAsahi/airco-tracking-web --body AzureManagedDomain
gh variable set EMAIL_MIN_SEND_INTERVAL_SECONDS --repo ProgrammerAsahi/airco-tracking --body 13
gh variable set EMAIL_MAX_REPLICAS --repo ProgrammerAsahi/airco-tracking --body 1
gh workflow run deploy.yml --repo ProgrammerAsahi/airco-tracking --ref main
gh workflow run deploy.yml --repo ProgrammerAsahi/airco-tracking-web --ref main
```

确认两个 deployment，再验证登录验证邮件 canary；当存在 active entitled recipient 时，还要验证 targeted stock-alert canary。排查期间保持 custom domain linked；未被应用选中的 linked domain 不会强迫任何一个应用使用它。只有确认两个应用都已使用 `AzureManagedDomain` 后才考虑断开：

```bash
MANAGED_DOMAIN_ID="$(az communication email domain show --resource-group airco-tracker-rg --email-service-name <discovered-email-service-name> --name AzureManagedDomain --query id --output tsv)"
az communication update --resource-group airco-tracker-rg --name <discovered-communication-service-name> --linked-domains "$MANAGED_DOMAIN_ID"
```

常规 rollback 不应删除 custom domain 或 DNS records。保留它们可以维持 verification，并支持可控的 forward fix。

## Microsoft 参考资料

- [自动化 Email Communication resource 和 domain 管理](https://learn.microsoft.com/azure/communication-services/samples/email-resource-management)
- [连接 verified email domain](https://learn.microsoft.com/azure/communication-services/quickstarts/email/connect-email-communication-resource)
- [Azure Communication Services service limits](https://learn.microsoft.com/azure/communication-services/concepts/service-limits)
- [提高 Email quota](https://learn.microsoft.com/azure/communication-services/concepts/email/email-quota-increase)
- [Sender authentication best practices](https://learn.microsoft.com/azure/communication-services/concepts/email/email-authentication-best-practice)
- [Sender reputation 和 managed suppression](https://learn.microsoft.com/azure/communication-services/concepts/email/sender-reputation-managed-suppression-list)
- [ACS Email Event Grid delivery reports](https://learn.microsoft.com/azure/event-grid/communication-services-email-events)
