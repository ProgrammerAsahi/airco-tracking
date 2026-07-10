# Airco Tracker — 当前交接

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

最后更新：2026-07-09（Europe/Amsterdam）

必须同时更新本文件和 `HANDOFF.md`。不要记录 secrets、邮箱地址、access tokens、支付数据或不必要的个人信息。

## 当前目标

运行一个可靠的便携空调追踪器，覆盖可配送到荷兰和法国地址的商品，并保留面向更多欧洲市场扩展的国家化设计。Scanner 每十分钟运行，持续更新私有库存快照；只有首次发现或重新补货的即时现货通过提醒筛选时，才产生库存事件。

当前改动把同步逐用户发信替换为 Azure Service Bus Standard 异步流水线。Subscriber 增长不得增加商家扫描延迟，邮件服务故障也不得阻塞库存和 state 的推进。

## 仓库和生产

- Repository：`https://github.com/ProgrammerAsahi/airco-tracking`
- Branch/local path：`main`、`~/airco-tracking`
- Resource group：`airco-tracker-rg`
- Frontend/auth repository：`https://github.com/ProgrammerAsahi/airco-tracking-web`
- Public site：`https://airco-tracker.eu/`
- Private inventory contract：Blob `airco-tracker/inventory.json`，schema version `1`
- Scanner job：`airco-tracker-job`，`*/10 * * * *` UTC
- Production mail provider：Azure Communication Services Email
- 纯文档 push 不会触发部署 workflow。

在本次交接快照中，Service Bus 实现位于前后端的协调 worktrees。Rollout 后必须在此记录准确的 immutable image SHA、GitHub run、Azure verification executions、定向 delivery event ID/status 和 queue/DLQ counts，之后才能标记为已发布。

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

Backend reconciler 支持旧 rows 的确定性 UUID 回填和安全/乐观并发删除规则。它是每日 repair path，不会让每个库存事件依赖完整 `users` 扫描。

## 安全和隐私

- 生产使用 Entra ID/OAuth 和 user-assigned Managed Identity。Service Bus 和 ACS 禁用 local authentication；Storage 默认 OAuth，Blob container 保持私有。
- Scanner/shared web runtime、publisher、fan-out 和 email delivery 使用相互分离的身份；新流水线权限在 Azure RBAC 支持的范围内限制到具体 entity/table。GitHub 通过 OIDC 和 least-privilege custom role 部署，不能创建 role assignments 或读取应用 secrets。
- Queue messages 不含邮箱、昵称、Stripe/customer/payment IDs 或卡片数据；`alertdeliveries` 也不保存地址。
- 邮箱只存在 canonical `users` 和最小化的 `alertrecipients` projection。Email worker 发信前实时解析，日志只输出遮蔽形式。
- 生产没有 `EMAIL_TO`/`notification-email` fallback。无法读取当前权益或地址时必须 fail closed。
- Key Vault 只存真正需要的第三方 adapter credentials；secret 不得进入 Git、镜像、Bicep parameters、Service Bus payload 或浏览器代码。

## 扩展性和当前 quota 限制

Scanner 工作量不随 subscriber 数量变化。Recipient expansion 独立扩缩容，并按 32 个 Table partitions 分页流式读取。Canonical `users` 只由每日 reconciler 读取，因此当前 hot path 不需要手工分表。

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

## 本次 release 所需验证

在 backend root 运行：

```bash
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
az bicep build --file infra/foundation.bicep --stdout >/dev/null
az bicep build --file infra/job.bicep --stdout >/dev/null
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

Recipient projection 是跨仓库改动，因此还需要在 `~/airco-tracking-web` 运行：

```bash
pnpm test
pnpm typecheck
pnpm build
```

真实定向测试必须遵循 `ALERT_PIPELINE.zh.md` 中的 Managed-Identity 一次性 execution 流程：先 reconcile recipients，再在已部署 publisher job 中用获得授权的匿名 `--recipient-id` 调用 `pipeline-test`，让生产 workers 完成消费。不要给个人 principal 临时添加 data-plane roles。完成条件：每个目标 delivery 达到 `sent`、确认 inbox 收到，并且 subscription 和两条 queues 的 active/dead-letter counts 回到零。不得在本 handoff 记录收件地址。

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

1. 完成后端和前端全量验证，compile/validate 两个 Bicep entry points。
2. 先部署 foundation/RBAC，重新 bootstrap OIDC，再 push/deploy 两个协调仓库。
3. 为两个授权账户执行 Managed-Identity 定向真实邮件测试，记录无 PII 证据及 queue/DLQ health。
4. 验证 customer-managed `airco-tracker.eu` ACS domain，并在大规模 onboarding 前申请生产 quota increase。
5. 为 nonzero DLQ、持续 queue age/backlog、stale pending outbox、Service Bus errors/throttling、delivery failure spikes 和 ACS quota responses 增加 Azure Monitor alert rules。

## 更新本 handoff

替换过期状态，不追加流水账。记录准确的 deployed commit/image、workflow/execution IDs、verification counts、剩余 blocker、frontend contract compatibility 和下一项行动。中英文文件必须同步，且不能包含 PII/secrets。
