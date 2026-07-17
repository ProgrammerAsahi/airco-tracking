# Airco Tracker — 异步提醒流水线

<p align="center">
  <a href="./ALERT_PIPELINE.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/ALERT_PIPELINE-简体中文-d73a49"></a>
  <a href="./ALERT_PIPELINE.md"><img alt="English" src="https://img.shields.io/badge/ALERT_PIPELINE-English-0969da"></a>
</p>

本文档是生产库存提醒链路的运维和安全基准。扫描与实时库存快照和邮件投递相互独立：邮件服务变慢或不可用时，不能拖慢商家扫描，也不能让库存页面变旧。

## 架构

```text
airco-tracker-job（每 10 分钟；持有唯一的分布式扫描租约）
  ├─ 写入私有 state.json 和 inventory.json
  └─ 向 alertoutbox 写入一条确定性的 stock.available.v1 事件
          │
airco-alert-publisher-job（每分钟）
          └─ stock-events topic → email-fanout subscription
                                      │
airco-alert-fanout-coordinator（0–4 replicas）
          └─ 32 个分片任务 → email-fanout-jobs queue
                                      │
airco-alert-fanout-worker（0–16 replicas）
          ├─ 每次流式读取一个 alertrecipients 分片
          ├─ 检查当前邮件权益和配送国家
          └─ 只含匿名 recipient UUID 的任务 → email-jobs queue
                                      │
airco-alert-email-worker（当前 0–1 replica）
          ├─ 发信前按 UUID 点读 canonical user
          ├─ 在 alertdeliveries 中认领 event × recipient
          └─ Azure Communication Services Email → accepted
                                                       │
ACS recipient delivery reports                        │
  └─ Event Grid system topic → acs-email-delivery-events queue
                                      │
airco-alert-delivery-worker（0–4 replicas）
          ├─ 关联确定性的 ACS message ID
          ├─ 记录 recipient-level final status
          └─ hard bounce 后 suppress 准确的 address fingerprint
```

前端/auth 服务是 `alertrecipients` 的主要写入方。注册、邮箱/语言/国家修改、Stripe 一次性 pass 支付/退款 webhook、pass 撤销和注销账户都会同步投影。`airco-alert-reconciler-job` 每天 `03:17 UTC` 运行一次，只用于修复跨表部分失败和回填旧用户，不位于每条事件的热路径上。

`airco-alert-retention-job` 每天 `02:17 UTC` 清理历史数据。Outbox publisher 每分钟运行，scanner 仍按 `*/10 * * * *` UTC 运行。

### 数据和 Service Bus entities

- `stock-events` topic / `email-fanout` subscription：一条商品库存变化事件。
- `email-fanout-jobs` queue：每个 recipient 分片一个任务。
- `email-jobs` queue：只含 `eventId` + `recipientId` 的投递任务。
- `acs-email-delivery-events` queue：独立、短期保存的 ACS recipient reports；与普通 queues 不同，provider-defined body 必然包含 recipient address。
- `alertoutbox`：持久事件内容和发布状态，按事件 hash 前缀分区。
- `alertrecipients`：32 个分区（`r-00` 到 `r-1f`）的最小邮件 read model。
- `alertdeliveries`：幂等、租约、尝试次数、终态和 ACS operation metadata。
- `alertdeliveryindex`：ACS message ID 到匿名 delivery binding 以及 address fingerprint；不存明文地址。
- `alertsuppression`：对准确 address fingerprint 生效的 recipient-scoped hard-bounce suppression；不存明文地址。
- `users`：由 Web 服务拥有的 canonical 账户/pass 权益表；每条事件的处理链路不会扫描它。

32 分片规则是跨仓库契约。Web 投影写入器和本后端都使用 `sha256(userId)` 的最低五位，且 `ALERT_RECIPIENT_SHARDS` 会被强制验证为 `32`。修改它必须同时在两个仓库做有版本的迁移。

Pass 迁移必须先部署能同时读取 pass 与旧 subscription 字段的后端，再部署改为写入 pass 字段的 Web 服务。两个 revision 均健康后运行一次 reconciliation，把剩余 projection rows 替换成新的最小权益结构。顺序反转可能在短时间内错误 suppress 合法收件人。

## 投递语义

流水线是 at-least-once，每一层都通过幂等设计抵抗重复：

- 真实库存事件 ID 是 `sha256(event type + country-scoped product key + availability generation)`。同一轮有货状态的重复扫描不会产生新的逻辑事件。
- Service Bus 启用七天 duplicate detection，每条消息都使用确定性 `MessageId`。
- Delivery ID 由 `eventId + recipientId` 派生。`alertdeliveries` 通过 ETag 状态转换和短租约，防止并行 worker 主动重复发送。
- ACS 接收确定性 operation ID 和 repeatability headers，缩小“邮件服务已接受、ledger 尚未落盘”之间的 crash window。
- ACS operation 成功后，Email worker 先记录 `accepted`；之后 Event Grid 才把 ledger 推进到 recipient-level final state：`delivered`、`expanded`、`bounced`、`provider_suppressed`、`quarantined`、`filtered_spam` 或 `provider_failed`。ACS accepted 绝不能被表述为已经进入收件箱。
- 调用 ACS 前，worker 会先创建 message-ID correlation row。Final reports 按 Event Grid event ID 幂等处理，并拒绝更旧的 out-of-order evidence。Hard bounce 或 provider suppression 会启用 recipient/address-fingerprint suppression，两个发信前检查都会执行；同一 fingerprint 上更新的 delivered report 可以清除 suppression。旧地址的 report 不能 suppress 用户后来验证的新地址。
- 瞬时邮件错误按退避策略重新排队；永久错误进入 dead letter。应用最多尝试发送五次，Service Bus `maxDeliveryCount` 为八次。
- 每次发送前 worker 都会按 UUID 点读 canonical `users/id:<uuid>` profile，并在 sender rate wait 之后再读一次。对于旧的 email-keyed profile，reconciler 会在 recipient projection 中保存私有 `sourceUserRowKey`，让 worker 点读 canonical row，并严格重新派生 UUID、确认与请求的 UUID 一致后才信任该 row。只有尚未回填该 pointer 的旧 projection 才使用有界 `userId` query fallback。因此即使 fan-out projection 尚在追赶，邮箱变更也会立即以 canonical 为准；pass 过期、退款或撤销、账户删除、配送国家改变，或生产事件超过六小时时，会标记 suppressed 而不是发送。迁移期仍兼容读取旧 recurring-subscription 字段，但只要任一 pass 字段存在便以新字段为权威，旧订阅数据不能恢复已撤销的权益。

跨外部邮件服务的分布式系统无法承诺数学意义上的 exactly-once，但确定性的 provider request 和 delivery ledger 让重复邮件很难发生并且可追查。

## 安全和隐私

- 生产通过 Entra ID 和 user-assigned Managed Identity 访问资源。Storage、Service Bus 和 ACS 在支持的范围内禁用本地/shared-key authentication；镜像和 GitHub 中不保存 connection string 或 ACS key。
- Scanner/shared web runtime、outbox publisher、fan-out 和 email delivery 使用相互分离的身份。新流水线权限在 Azure RBAC 支持的范围内限制到具体 Service Bus entity 或 Table；不要把 workers 合并成一个宽泛的 Contributor 身份。
- `stock-events` 只含商品和配送范围，不含 subscriber 数据。
- Service Bus fan-out/email 消息只含稳定的匿名 recipient UUID，不含邮箱、昵称、Stripe customer/payment ID、支付方式或卡片信息。
- 独立的 ACS delivery-report queue 是唯一受严格限制的 PII 例外：Microsoft event schema 必然包含 recipient address。一日 TTL、私有七日 Event Grid dead-letter container、每日 Service Bus DLQ purge、独立 least-privilege identity 和禁止记录 body 的日志约束共同限制了风险。Azure Event Grid 会在 storage-account scope 校验 dead-letter 权限，因此其 managed identity 在 state account 上具有 `Storage Blob Data Contributor`，但实际 endpoint 仍是专用私有 container；该 identity 没有 application tables reader，应用也不会使用它。绝不能把 raw provider report 复制进 ticket、普通日志或其它 queue。
- `alertrecipients` 是提醒专用表中唯一保存邮箱的表，只保留决定和生成邮件所必需的字段：稳定 user ID、邮箱、语言、配送国家、`entitlementTier`（`alerts` 或 `radar`）、`entitlementStatus`、`entitlementExpiresAt`、enabled、同步时间，以及仅用于旧 profile 点读的私有 canonical source-row pointer。购买时间和 Stripe identifiers 会被明确排除。
- `alertdeliveries`、`alertdeliveryindex` 和 `alertsuppression` 只保存匿名 ID、投递状态和 pseudonymous address fingerprints，不保存目标邮箱；应用日志会遮蔽邮箱 local part，final-report logs 只使用匿名 delivery ID。
- Email worker 会在发信前从 canonical profile 解析最新地址；其 identity 对 `users` 只有只读权限，代码只消费投递字段。私有 source-row pointer 不得进入 Service Bus message、日志、重试 metadata 或 API。
- 库存 Blob container 必须保持私有。浏览器只能通过前端同源 API 和 Managed Identity 读取。
- 本地 `ALERT_DISPATCH_BACKEND=direct` 只用于兼容开发环境。Azure 生产必须使用 `service_bus`，无法确认 recipient 状态时 fail closed。

## 数据保留

- Service Bus stock/fan-out 消息：TTL 一天；过期后进入 dead letter。
- Email jobs：TTL 六小时；应用也会 suppress 超过六小时的生产事件。
- ACS delivery reports：queue TTL 一天，过期时不进入 DLQ；达到 delivery attempts 上限的消息由每日 privacy job 从专用 Service Bus DLQ 删除。
- Event Grid delivery-report dead letters：私有 Blob container，通过 lifecycle rule 在七天后自动删除。
- 已发布 `alertoutbox` rows：30 天。
- No-resend/final `alertdeliveries` rows（`accepted`、provider final results、旧 `sent`、业务 `suppressed` 和 `failed`）：90 天。若任一 `accepted` row 等待 final report 超过两小时，每日任务会输出只含数量、不含用户信息的 warning。
- `alertdeliveryindex` correlation rows：与 delivery metadata 一样保留 90 天。
- `alertsuppression` 每个 recipient/address state 最多一条；canonical profile 不存在或不再 active 时，每日任务会删除该 row。旧 profile 的 canonical source pointer 尚未回填时会保守保留，待 reconciler 明确状态后再清理。
- Pending outbox 或非终态 delivery 不会按年龄删除，必须保留用于恢复或调查。
- Log Analytics workspace：30 天。
- `alertrecipients` 随账户生命周期管理；用户注销时删除。每日 reconciler 只有在完整扫描 canonical users 后才会删除失效的 projection rows。

只有在重新评估事故排查和隐私需求后才能修改 `ALERT_OUTBOX_RETENTION_DAYS` 或 `ALERT_DELIVERY_RETENTION_DAYS`。手动清理命令：

```bash
.venv/bin/python -m airco_tracker cleanup-alert-data --limit 5000
```

## 容量和扩展

Subscriber 数量不再影响 scanner 延迟。扫描对每个满足条件的商品变化最多写一条事件，独立 worker 负责展开收件人并投递。

- Recipient rows 分散在 32 个 Azure Table partitions，并按每页 250 条流式读取；不会把所有订阅用户一次性载入内存。
- Coordinator 最多扩到 4 replicas，fan-out workers 最多 16。应先观察 Table 和 Service Bus throttling 再继续上调。
- Standard tier 的 topic 和两个 queues 都按 16 partitions 创建。提醒消息不需要全局顺序，因此 partitioning 可以移除单一 broker/entity 的吞吐瓶颈并提高可用性。每个 batch 只使用一个确定性的 partition key（stock bucket、event 或 recipient shard），既保留 duplicate detection，也不会在同一个 Service Bus batch 里混用 partition keys。Azure 无法在实体创建后原地修改这个开关；部署这项 foundation 变更前，应迁移或重建已经确认为空的实体。
- `enableServiceBusPartitioning` 是 foundation 创建/回滚参数；Azure 仍无法原地修改，因此切换时必须先删除或版本化已经确认为空的 entities。
- Canonical `users` table 不会按每条库存事件扫描；每日 reconciler 只作为 repair job 流式读取，email worker 则对每次实际投递做常数时间 UUID 或 legacy-source-row point read。只有尚未回填 source pointer 的旧 projection 会暂时使用有界 `userId` query 兼容路径。当前提醒热路径不需要手工分表。
- Service Bus 使用 Standard、partition-safe batching 和 duplicate detection。应持续监控 namespace throttling 与队列年龄；当 shared-tier 延迟或 Standard namespace 的 operation ceiling 成为实际瓶颈时，再迁移到 partitioned Premium namespace。
- 生产使用已验证的 customer-managed ACS sender domain `airco-tracker.eu`。官方文档中的默认限制是每分钟 30 封、每小时 100 封。Final-delivery、bounce、suppression 和 alert monitoring 已投入运行；在 Open 的 higher-quota request 获批前，email worker 仍故意限制为一个 replica，并在同一进程的两次发送之间等待 13 秒。当前端到端吞吐瓶颈仍是 provider quota，而不是 Service Bus 或 Table Storage。

Domain/SPF/DKIM/DKIM2 验证、Communication Service 连接、`ACS_EMAIL_DOMAIN_NAME` 明确选择以及 Gmail/Outlook 真实收件箱 canary 均已完成。Foundation 会同时保留自定义域和 Azure-managed fallback，部署脚本按域名明确选择，不依赖 `linkedDomains` 数组顺序。Reply-To、用户提醒开关、RFC 8058 unsubscribe、recipient-level final-delivery ingestion、hard-bounce suppression、privacy cleanup 与相关 monitoring 已部署并完成生产验证。ACS quota case `06bfd9d3-65c22af0-6d841855-b8dc-4aea-8d93-d2364a875032` 当前为 Open，申请 tier `250`（1,000 封/分钟、3,000 封/小时）。Azure 批准前保持 `EMAIL_MIN_SEND_INTERVAL_SECONDS=13` 和 `EMAIL_MAX_REPLICAS=1`；不能先提高 replicas，否则只会产生 ACS `429` 和队列反复重试。DNS、consent、final-delivery、suppression、monitoring、quota 和 warm-up 详见 [EMAIL_DELIVERY.zh.md](./EMAIL_DELIVERY.zh.md)；自定义域 rollback 仍见 [ACS_CUSTOM_EMAIL_DOMAIN.zh.md](./ACS_CUSTOM_EMAIL_DOMAIN.zh.md)。

关键容量信号包括：全部 queues/subscriptions 的 active message count 与 oldest-message age、dead-letter count、Service Bus throttled requests/server errors、Event Grid delivery failures/drops/dead letters、accepted → final latency、final-status rates、outbox pending age，以及 ACS `429`/quota responses。Service Bus diagnostics 和 metrics 已写入 Log Analytics。生产有四条 namespace alerts（`aircontrack-servicebus-deadletter`、`aircontrack-servicebus-backlog`、`aircontrack-servicebus-throttled` 和 `aircontrack-servicebus-server-errors`），以及三条覆盖 delivery failure、dropped event 和 dead-lettered report 的 Event Grid alerts。另有两条 privacy-safe scheduled-query alerts，分别覆盖 accepted 超过两小时仍无 final report，以及 adverse provider outcomes。Enabled rules 绑定到 `aircontrack-operations-alerts` Action Group。Outbox-age、ACS-quota-spike alerts 和持续的端到端 inbox canaries 仍属于后续加固工作。

## 配置

`infra/job.bicep` 会注入生产配置。不要把凭据手工复制到环境变量。

```text
ALERT_DISPATCH_BACKEND=service_bus
SERVICE_BUS_NAMESPACE=<namespace>.servicebus.windows.net
STOCK_EVENTS_TOPIC=stock-events
STOCK_EVENTS_SUBSCRIPTION=email-fanout
FANOUT_JOBS_QUEUE=email-fanout-jobs
EMAIL_JOBS_QUEUE=email-jobs
ACS_DELIVERY_EVENTS_QUEUE=acs-email-delivery-events
AUTH_USERS_TABLE=users
ALERT_OUTBOX_TABLE=alertoutbox
ALERT_RECIPIENTS_TABLE=alertrecipients
ALERT_DELIVERIES_TABLE=alertdeliveries
ALERT_DELIVERY_INDEX_TABLE=alertdeliveryindex
ALERT_SUPPRESSIONS_TABLE=alertsuppression
ALERT_RECIPIENT_SHARDS=32
ALERT_RECIPIENT_PAGE_SIZE=250
ALERT_EVENT_MAX_AGE_SECONDS=21600
ALERT_OUTBOX_RETENTION_DAYS=30
ALERT_DELIVERY_RETENTION_DAYS=90
SCANNER_LEASE_SECONDS=480
EMAIL_MIN_SEND_INTERVAL_SECONDS=13
EMAIL_MAX_REPLICAS=1
ACS_EMAIL_DOMAIN_NAME=airco-tracker.eu
EMAIL_REPLY_TO=support@airco-tracker.eu
APP_BASE_URL=https://airco-tracker.eu
EMAIL_UNSUBSCRIBE_SIGNING_KEY=<Key Vault secret reference；绝不能填写 production 明文>
```

Bicep 还会注入 `AZURE_STORAGE_ACCOUNT_URL`、`AZURE_CLIENT_ID`、`ACS_ENDPOINT`、`EMAIL_FROM` 和正常 scanner 配置。`EMAIL_TO` 只属于本地/direct mode，不是生产 subscriber 数据源。

`operationsAlertEmail` 是 secure foundation parameter，不是 application environment variable。首次配置 foundation 时，只能在本地通过 `AZURE_OPERATIONS_ALERT_EMAIL` 提供；不得把邮箱提交到仓库或保存为 GitHub Actions variable。以后再次运行 `deploy-azure.sh` 时，不设置该变量会让脚本从 `aircontrack-operations-alerts` 读取并保留现有 `primary-operations-mailbox` receiver。如果从未配置 receiver，四条 metric alerts 仍会在 Azure dashboard 中保持 enabled，但在传入 secure parameter 前不会执行 email action。

## 部署顺序

Foundation deployment 会创建资源和 RBAC，因此必须由有权创建 role assignments 的 Azure principal 运行。GitHub deployer 刻意没有这项权限。

新环境或 foundation/RBAC 改动的顺序：

```bash
cd ~/airco-tracking
az login
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

`deploy-azure.sh` 会注册 providers、部署 `infra/foundation.bicep`、构建镜像、部署 `infra/job.bicep`，并按依赖顺序启动 verification jobs。新建 RBAC 可能需要数分钟传播；若首次 application deployment 因此失败，请等待后重试。

首次配置运维 receiver 时，只在运行 `deploy-azure.sh` 的本地环境设置 `AZURE_OPERATIONS_ALERT_EMAIL`，部署后立即 unset。ARM 会把映射的 `operationsAlertEmail` 当作 secure parameter；以后不再提供地址也会保留原 receiver。

普通 application-only release 由 push 到 `main` 触发：测试、构建 commit SHA 不可变镜像、部署 jobs/apps，然后验证 recipient reconciliation、scanner 和 outbox publish。纯 Markdown 改动不会触发部署。手动部署命令：

```bash
IMAGE_TAG="$(git rev-parse --short=12 HEAD)" \
AZURE_RESOURCE_GROUP=airco-tracker-rg \
./scripts/deploy-application.sh
```

Foundation 改动后需要再次运行 `bootstrap-github-oidc.sh`，确保 GitHub least-privilege custom deployer role 包含当前资源类型和 actions，然后才能依赖 GitHub Actions。

## 验证和定向真实邮件测试

以下本地检查不会发送生产邮件：

```bash
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
az bicep build --file infra/foundation.bicep --stdout >/dev/null
az bicep build --file infra/job.bicep --stdout >/dev/null
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

真实邮件 synthetic test 必须严格定向，并通过已经部署的 publisher Managed Identity 运行。**不要**给开发者/个人 Azure principal 临时添加 Table、Service Bus 或 ACS data-plane roles，也不要在命令、YAML、日志或 Service Bus 中放邮箱地址。只能通过获得授权的账户/reconciliation 流程取得准确的稳定 recipient UUID，并且只使用 `--recipient-id`。

先运行使用 Managed Identity 的 reconciler，并确认 execution 成功。然后从当前已部署 publisher template 生成权限为 `0600` 的一次性 execution template，只把 command args 替换为获得授权的匿名 UUID，再启动它。`job start --yaml` 只启动一次 execution，不会修改保存的 image、schedule、identity 或正常 publisher args：

```bash
RESOURCE_GROUP=airco-tracker-rg
PUBLISHER_JOB=airco-alert-publisher-job
RECIPIENT_ID_1='<authorized-recipient-uuid-1>'
RECIPIENT_ID_2='<authorized-recipient-uuid-2>'

RECONCILE_EXECUTION="$(az containerapp job start \
  -g "$RESOURCE_GROUP" -n airco-alert-reconciler-job \
  --query name -o tsv)"
echo "Reconciler execution: $RECONCILE_EXECUTION"
# 继续前先等待该 execution 报告 Succeeded。

command -v jq >/dev/null || { echo 'jq is required.' >&2; exit 1; }
TEST_YAML="$(mktemp /tmp/airco-pipeline-test.XXXXXX.yaml)"
chmod 600 "$TEST_YAML"
trap 'rm -f "$TEST_YAML"' EXIT

az containerapp job show -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --query properties.template -o json \
  | jq --arg first "$RECIPIENT_ID_1" --arg second "$RECIPIENT_ID_2" '
      .containers[0].command = ["airco-tracker"]
      | .containers[0].args = [
          "pipeline-test",
          "--recipient-id", $first,
          "--recipient-id", $second
        ]
    ' > "$TEST_YAML"

TEST_EXECUTION="$(az containerapp job start \
  -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --yaml "$TEST_YAML" --query name -o tsv)"
rm -f "$TEST_YAML"
trap - EXIT
echo "Targeted test execution: $TEST_EXECUTION"

az containerapp job logs show -g "$RESOURCE_GROUP" -n "$PUBLISHER_JOB" \
  --execution "$TEST_EXECUTION" --container outbox-publisher \
  --tail 50 --format text
```

保持正常 scanner 和 publisher schedules 不变。只读检查 worker logs 和 broker backlogs，不要 receive 或 purge messages；只记录 event/delivery IDs 和 counts，绝不能记录收件地址。成功标准是：定向 execution 成功、两个 delivery rows 都从 `accepted` 推进到 recipient-level final status、两个 inbox 都实际收到邮件，并且 subscription 与全部三条 queues 的 active/dead-letter counts 回到零；还要确认可见退订链接和 RFC 8058 one-click 路径都只暂停邮件、不改变付费权益。只有 ACS accepted 不代表 inbox 已投递。

## 运维

手动运行修复和清理 jobs：

```bash
az containerapp job start -g airco-tracker-rg -n airco-alert-reconciler-job
az containerapp job start -g airco-tracker-rg -n airco-alert-retention-job
```

查看 job executions 和 worker logs：

```bash
az containerapp job execution list -g airco-tracker-rg -n airco-tracker-job -o table
az containerapp job logs show -g airco-tracker-rg -n airco-tracker-job --follow
az containerapp logs show -g airco-tracker-rg -n airco-alert-fanout-worker --follow
az containerapp logs show -g airco-tracker-rg -n airco-alert-email-worker --follow
az containerapp logs show -g airco-tracker-rg -n airco-alert-delivery-worker --follow
```

只查看 Service Bus backlog，不接收消息：

```bash
az servicebus topic subscription show -g airco-tracker-rg \
  --namespace-name <namespace> --topic-name stock-events -n email-fanout \
  --query countDetails
az servicebus queue show -g airco-tracker-rg \
  --namespace-name <namespace> -n email-fanout-jobs --query countDetails
az servicebus queue show -g airco-tracker-rg \
  --namespace-name <namespace> -n email-jobs --query countDetails
az servicebus queue show -g airco-tracker-rg \
  --namespace-name <namespace> -n acs-email-delivery-events --query countDetails
```

每日 retention job 清理普通 metadata；独立 privacy job 则删除进入专用 Service Bus DLQ 的 raw final-report messages：

```bash
az containerapp job start -g airco-tracker-rg -n airco-delivery-dlq-cleanup
```

不读取或修改 receiver 地址，只检查四条启用的 alert rules 和 Action Group：

```bash
az monitor metrics alert list -g airco-tracker-rg \
  --query "[?starts_with(name, 'aircontrack-servicebus-')].{name:name,enabled:enabled}" \
  -o table
az monitor action-group show -g airco-tracker-rg \
  -n aircontrack-operations-alerts \
  --query "{name:name,enabled:enabled,receiverCount:length(emailReceivers)}"
```

不要盲目 purge 或 replay dead-letter queue。先记录 dead-letter reason，修复永久性的 payload/configuration 问题；只有确定性 event/delivery IDs 能保证重放安全时才可 replay。没有 dead letters 但持续积压通常表示 capacity/quota 压力；dead-letter count 增长则表示无效 payload 或永久性投递失败。
