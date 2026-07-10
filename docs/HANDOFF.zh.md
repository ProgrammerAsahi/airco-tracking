# Airco Tracker — 当前交接

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

最后更新：2026-07-10（Europe/Amsterdam）

必须同时更新本文件和 `HANDOFF.md`。不要记录 secrets、邮箱地址、access tokens、支付数据或不必要的个人信息。

## 当前目标

运行一个可靠的便携空调追踪器，覆盖可配送到荷兰和法国地址的商品，并保留面向更多欧洲市场扩展的国家化设计。Scanner 每十分钟运行，持续更新私有库存快照；只有首次发现或重新补货的即时现货通过提醒筛选时，才产生库存事件。

已发布架构把同步逐用户发信替换为 Azure Service Bus Standard 异步流水线。Subscriber 增长不会增加商家扫描延迟，邮件服务故障也不会阻塞库存和 state 的推进。

## 仓库和生产

- Repository：`https://github.com/ProgrammerAsahi/airco-tracking`
- Branch/local path：`main`、`~/airco-tracking`
- Resource group：`airco-tracker-rg`
- Frontend/auth repository：`https://github.com/ProgrammerAsahi/airco-tracking-web`
- Public site：`https://airco-tracker.eu/`
- Private inventory contract：Blob `airco-tracker/inventory.json`，schema version `1`
- Scanner job：`airco-tracker-job`，`*/10 * * * *` UTC
- Publisher job：`airco-alert-publisher-job`，`* * * * *` UTC
- Production mail provider：Azure Communication Services Email
- 已部署 backend image/commit：`bfe6b407be84831cf961149cc617956945174ab0`（核心流水线 commit `cd8acbb2aa9544b2d6c79d072c9a3373323da9f3`）
- 兼容 frontend commit：`715acf223377d6b450a2a594e32eee0515a85797`
- 成功的 backend workflow runs：`29060991005`、`29063024406`；成功的 frontend run：`29061171454`
- Foundation migration deployment：`airco-foundation-partition-migration-20260710`
- GitHub 生产暂停变量：`DEPLOYMENT_PAUSED=false`
- 纯文档 push 不会触发部署 workflow。

## 异步提醒流水线

完整设计和 runbook 见 [ALERT_PIPELINE.zh.md](./ALERT_PIPELINE.zh.md)。生产流程：

1. Scanner 持有分布式 lease，更新 Blob snapshot/state，并在推进 alert state 前把确定性的 `stock.available.v1` 事件持久化到 `alertoutbox`。
2. `airco-alert-publisher-job` 每分钟把 pending rows 发布到 `stock-events` topic。
3. `airco-alert-fanout-coordinator` 消费 `email-fanout` subscription，在 `email-fanout-jobs` 中创建 32 个 shard jobs。
4. 最多 16 个 fan-out workers 流式读取相应 `alertrecipients` partition，并向 `email-jobs` 写入只含匿名 recipient ID 的 jobs。
5. Email worker 重新读取最新 recipient、再次检查权益/国家/event age、以 ETag 保护的方式认领 `alertdeliveries` row，再用确定性 operation ID 通过 ACS 发送。

辅助 schedules：

- `airco-alert-reconciler-job`：每天 `03:17 UTC`，从 canonical `users` 修复由 Web 维护的投影。
- `airco-alert-retention-job`：每天 `02:17 UTC`，30 天后删除已发布 outbox rows，90 天后删除终态 delivery rows。

Service Bus stock/fan-out 消息 TTL 一天；email jobs 和应用 event freshness 为六小时；duplicate detection 为七天。无效/永久失败消息进入 dead letter，而不是被静默完成。

## 跨仓库 recipient contract

Web/auth 改动为每个用户加入稳定 UUID `userId`；修改邮箱不会改变此 ID。注册、Profile preferences、Stripe subscription webhooks、取消订阅和注销账户都会同步 `alertrecipients`。

Projection contract 固定为 32 partitions（`r-00`…`r-1f`），使用 `sha256(userId)` 的最低五位。它只保存提醒所需的最新邮箱、语言、配送国家、plan/status/period end、enabled 和同步 metadata。改变 shard count 必须在两个仓库进行协调、版本化迁移。

Backend reconciler 支持旧 rows 的确定性 UUID 回填、记录用于常数时间权威投递读取的私有 canonical source-row pointer，并使用安全/乐观并发删除规则。它是每日 repair path，不会让每个库存事件依赖完整 `users` 扫描。只有在旧 source row 重新派生出的 UUID 与请求的 recipient UUID 完全一致时才会信任该 row。

## 安全和隐私

- 生产使用 Entra ID/OAuth 和 user-assigned Managed Identity。Service Bus 和 ACS 禁用 local authentication；Storage 默认 OAuth，Blob container 保持私有。
- Scanner/shared web runtime、publisher、fan-out 和 email delivery 使用相互分离的身份；新流水线权限在 Azure RBAC 支持的范围内限制到具体 entity/table。GitHub 通过 OIDC 和 least-privilege custom role 部署，不能创建 role assignments 或读取应用 secrets。
- 旧的 storage-account 级 `Storage Table Data Contributor` 已移除。Shared runtime 只保留所需的逐表 contributor/reader 和 Blob 权限；移除后生产 OTP、Profile/投影写入、登出、retention 和 scanner execution 全部通过。
- Queue messages 不含邮箱、昵称、Stripe/customer/payment IDs、卡片数据或私有 canonical source-row pointer；`alertdeliveries` 也不保存地址。
- 邮箱只存在 canonical `users` 和最小化的 `alertrecipients` projection。Email worker 发信前实时解析，日志只输出遮蔽形式。
- 生产没有 `EMAIL_TO`/`notification-email` fallback。无法读取当前权益或地址时必须 fail closed。
- Key Vault 只存真正需要的第三方 adapter credentials；secret 不得进入 Git、镜像、Bicep parameters、Service Bus payload 或浏览器代码。

## 扩展性和当前 quota 限制

Scanner 工作量不随 subscriber 数量变化。Recipient expansion 独立扩缩容，并按 32 个 Table partitions 分页流式读取。Canonical `users` 只由每日 reconciler 流式扫描；email worker 每次实际投递只做一次权威 point read（UUID row，或 reconciler 记录的旧 source row）。只有尚未回填的旧 projection 才使用有界兼容 query。因此当前 hot path 不需要手工分表。

Coordinator 最多 4 replicas，fan-out 最多 16。Service Bus Standard entities 使用 batching 和确定性 duplicate detection。调整拓扑前要先监控 backlog age、active/dead-letter counts、throttling、pending outbox age、delivery failures 和 ACS `429`。

当前 Azure-managed ACS sender domain 是瓶颈：约 5 messages/minute、10/hour。因此 email app 限制为一个 replica，两次发送至少间隔 13 秒。正式增长前，应验证 `airco-tracker.eu` customer-managed ACS sender（SPF/DKIM），通过 foundation 的 `customEmailDomainId` 连接，并用 `ACS_EMAIL_DOMAIN_NAME` 明确选中它，再申请 quota increase、提高 email replicas/rate。Quota 未提高前增加 worker 数量是不安全的。

## 库存和 retailer 语义

- 当前有 45 个无需凭据的 active adapters：荷兰 28 个、法国 17 个。README 是 active list 和 per-retailer notes 的权威记录。
- 只追踪真实压缩机空调。排除 air coolers、风扇、配件、quote-only items、不在受支持 portable scope 内的 fixed split、仅门店/仅自取、过期 deals 和多周交期。
- 预售可以进入 dashboard，但不能触发现货邮件；预售转即时现货是有效 restock transition。
- 单一 retailer 失败不能阻断其它站点。失败站点保留上次成功库存并标记 `status: error` / `stale: true`；alert state 只为成功站点更新。
- Live inventory 与 alert state 分离。Inventory schema version `1` 是生产跨仓库契约；breaking change 必须显式 bump version 并协调前后端 release。
- Direct 403/anti-bot candidates 记录在 [RETAILER_403_BACKLOG.zh.md](./RETAILER_403_BACKLOG.zh.md)。不得绕过 CAPTCHA、robots restrictions、login walls 或 anti-bot controls。

## 外部 API 状态

- Conrad storefront 被 Cloudflare 阻挡。只有 allowlist/approval 后才能使用官方 Price & Availability API；不得恢复反爬 scraping。
- AliExpress affiliate access 已批准，但实现前仍需重新确认 Open Platform application/key/官方 signing 状态。只读取 catalog/affiliate scopes，不收集 buyer、order、payment 或其它个人数据。

## 本次 release 已完成验证

- Backend：169/169 unit tests、compileall、shell syntax、两个 Bicep entry points 和 `git diff --check` 全部通过。
- Frontend：59/59 tests、typecheck、production build、Bicep/deployment verification 和生产 HTTP checks 全部通过。
- GitHub 已成功部署 immutable SHAs。Service Bus topic 和两条 queues 均为 `Active`、已 partition、使用七天 duplicate detection；subscription lock 为五分钟，`maxDeliveryCount=8`。
- 生产定向事件 `f13967a78d7da2d2c1590d419fffbe969cdd4864175b2a8132cef8afc8a133c6` 由 `airco-alert-publisher-job-8e1cnu7` 执行。Deliveries `3595247c55c2…`、`21514ad2068d…` 均达到 `sent`，ACS 接受两封邮件，两个已授权 inbox 均实收；此处不记录地址。
- 之前一次 fail-closed preflight 暴露了缺少 canonical UUID source pointer 的旧 rows；它没有发出邮件。Commit `bfe6b40` 加入严格的 legacy source-row 解析，随后定向测试成功。
- 移除宽泛 Storage Table 权限后，真实 OTP 登录、语言写入/恢复、投影同步和登出均返回 200。Retention execution `airco-alert-retention-job-6u70ukl` 与 scanner execution `airco-tracker-job-ncdtvul` 成功；scanner 保存了 45 个站点的 75 个现货商品并持久化 4 个真实 outbox transitions。
- 恢复正常 schedules 后，这些 transitions 完整流经流水线（fan-out backlog 峰值 128、email backlog 峰值 2）。ACS 接受 deliveries `3548668d33cb…`、`00eda20addd6…`；两封邮件均实际到达，但 Gmail 把 Azure-managed-domain 邮件归入 Spam，Outlook 则进入收件箱。Subscription 和两条 queues 最终 active、scheduled、transfer-DLQ、DLQ 全为零。
- 自定义域名最终检查：`/`、`/health` 和 `www` health 返回 200；匿名 `/api/inventory` 按要求返回 401。

## 部署顺序

Foundation/RBAC 必须由 Owner 或同等具有 role-assignment 权限的本地 principal 运行；GitHub deployer 被刻意限制为无法创建 RBAC：

```bash
cd ~/airco-tracking
az login
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

普通 application release 由 push `main` 触发：测试、构建 SHA-tagged immutable image、部署 jobs/apps，并验证 reconciler → scanner → publisher。新 RBAC 尚未传播时应等待后重跑 `scripts/deploy-application.sh`，不能扩大权限绕过。

## 下一步

1. 验证带 SPF/DKIM 的 customer-managed `airco-tracker.eu` ACS domain，并在大规模 onboarding 前申请生产 quota increase。生产 scanner 已能投递，但最新 Gmail canary 因 Azure-managed sender 进入了 Spam。
2. 保持已部署的四条 Service Bus namespace alerts 启用；补充 stale pending outbox、delivery-failure spikes、ACS `429` 和定时端到端 inbox canary 等应用级告警。
3. 继续观察最新 scanner 中 GAMMA 与 KARWEI 的 parser drift；在修复前，它们上次成功库存会安全保留并标记 stale。

## 更新本 handoff

替换过期状态，不追加流水账。记录准确的 deployed commit/image、workflow/execution IDs、verification counts、剩余 blocker、frontend contract compatibility 和下一项行动。中英文文件必须同步，且不能包含 PII/secrets。
