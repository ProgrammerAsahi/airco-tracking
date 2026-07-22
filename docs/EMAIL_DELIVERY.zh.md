# Airco Tracker — 邮件投递、同意与发件信誉

<p align="center">
  <a href="./EMAIL_DELIVERY.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/EMAIL_DELIVERY-简体中文-d73a49"></a>
  <a href="./EMAIL_DELIVERY.md"><img alt="English" src="https://img.shields.io/badge/EMAIL_DELIVERY-English-0969da"></a>
</p>

本文档是登录验证邮件和库存提醒邮件在运维、隐私与送达率方面的基准，并补充 [ALERT_PIPELINE.zh.md](./ALERT_PIPELINE.zh.md)。只有 handoff 已记录生产部署和验证的控制措施才能视为真正生效；仅在本地准备完成不等于已经投入生产。

## 当前生产状态（2026-07-22）

- Customer-managed ACS 发件域 `airco-tracker.eu` 已连接，并由两个应用明确选择。Domain ownership、SPF、DKIM 和 DKIM2 均已验证；DMARC 在观察信誉期间按计划保持 `p=none`。
- 真实登录验证邮件 canary 已分别进入 Gmail 与 Outlook 收件箱。原始邮件头确认了品牌发件域、对齐并通过的 SPF/DKIM、通过的 DMARC，以及 `Reply-To: support@airco-tracker.eu`。这些 canary 验证了生产 ACS 发件链路，同时不会在运维记录中暴露验证码或收件地址。
- 已部署的库存提醒链路使用 topic `stock-events`、subscription `email-fanout`，以及恰好三条 Service Bus queues：`email-fanout-jobs`、`email-jobs` 和限制 PII 范围的最终报告 queue `acs-email-delivery-events`。生产检查后，它们的 active 与 dead-letter count 均恢复为零。
- 最近一次发布验证时没有仍有效且拥有提醒权益的收件人，因此系统按设计没有产生库存提醒投递。完整的 subscriber-targeted canary 延后到存在真实、主动同意且权益有效的用户时执行；本次已经分别验证 direct ACS delivery、queue health 和 fail-closed recipient reconciliation。
- Higher-quota request 已提交，目前仍为 **Open/pending**；其私有 case identifier 不写入这个公开仓库。Azure 正式批准前，生产必须继续保持一个 email worker 和全局最短 13 秒发送间隔。

## 邮件身份与入站路由

- 发件身份：通过已验证的 Azure Communication Services（ACS）customer-managed domain 使用 `Airco Tracker <DoNotReply@airco-tracker.eu>`。
- 回复地址：`support@airco-tracker.eu`。登录验证邮件和库存提醒邮件都会把它设置为 `Reply-To`；envelope sender 仍使用 ACS 发件身份。
- DMARC aggregate report 地址：`dmarc@airco-tracker.eu`。它只接收自动生成的 aggregate XML 报告，不处理用户客服邮件。
- `support` 与 `dmarc` 是两个独立的 Dynadot 免费邮件转发 alias，分别路由到受监控的现有邮箱。实际目标邮箱属于运维秘密/PII，不得进入 Git、Bicep、应用设置、日志或本文档。
- 在依赖上述地址前，必须确保 Dynadot forwarding MX 可被公网解析；使用 `dig MX airco-tracker.eu` 并分别对两个 alias 做真实入站 canary。启用转发时不得替换现有网站 A/CNAME 或 ACS SPF/DKIM records。
- Dynadot 免费转发只能接收并转发邮件，目前整个域每天最多转发 500 封；这足以覆盖初始规模的客服与 aggregate reports。但如果没有另行配置经过认证的品牌邮箱服务，从目标邮箱回复时可能暴露该目标邮箱。

## SPF、DKIM 与 DMARC

ACS SPF、DKIM 和 DKIM2 必须持续保持 `Verified`，并与 `airco-tracker.eu` 对齐。域名 apex 只能存在一条 SPF TXT policy；以后增加发件服务时，应把授权合并进同一条 policy，不能再发布第二条 SPF。

DMARC 从仅观察模式开始：

```text
Host:  _dmarc
Type:  TXT
Value: v=DMARC1; p=none; rua=mailto:dmarc@airco-tracker.eu; pct=100
```

不要设置 `ruf`；forensic reports 可能带有邮件或收件人数据，初始阶段并不需要。确认 aggregate XML 报告进入受监控邮箱，并确认所有合法发件源都通过 SPF 或 DKIM alignment。在两到四周 warm-up 期间保持 `p=none`。只有报告历史确认不存在未识别的合法发件源，而且有明确 rollback owner 后，才能按计划先升级到 `quarantine`，再升级到 `reject`。

## Reply-To 行为

Backend 的 `EMAIL_REPLY_TO` 和 Web/auth 服务的 `AUTH_EMAIL_REPLY_TO` 必须解析为 `support@airco-tracker.eu`。应用通过 ACS 的结构化 `replyTo` 字段传入该地址。生产缺少或错误的 Reply-To 是 release blocker：用户必须能够联系到受监控地址，同时原始邮件不能暴露 Azure resource hostname 或个人邮箱。

## 用户同意与退订

库存提醒邮件偏好独立于付费 pass 和实时库存权益：

- 新用户默认 `emailAlertsEnabled=true`。对于旧 profile，字段缺失时仍解释为 enabled，以保持此前用户明确购买的提醒行为；只要明确保存为 `false`，fan-out 就必须 suppress 邮件。
- Profile 提供登录后的开启/暂停开关。暂停提醒邮件不会撤销 pass，也不会移除实时库存页面权限。
- 每封非测试库存提醒都包含可见退订链接，以及 RFC 8058 headers：`List-Unsubscribe` 和 `List-Unsubscribe-Post: List-Unsubscribe=One-Click`。
- RFC 8058 endpoint 只接受 `POST`、`application/x-www-form-urlencoded` 和 `List-Unsubscribe=One-Click`。操作必须幂等、不需要 session cookie，也不泄露账户是否存在。
- 浏览器可见链接先显示确认页面，再使用同一暂停操作；用户以后可以在 Profile 中重新开启。
- Capability token 只含稳定 UUID 和 token version，使用 HMAC-SHA-256 认证，绝不包含邮箱。Signing key 存在 Key Vault 中，两个仓库都通过 Managed Identity-backed secret reference 使用；不得进入 Git、镜像或浏览器 bundle。
- 更改邮箱或邮件提醒偏好都会递增 token version，使旧链接失效；注销账户后所有链接也自然失效。

这个 transactional consent 不能被用于营销或无关推广。收件地址只能来自自行注册、完成邮箱验证、持有未过期 `alerts` 或 `radar` pass 且尚未暂停提醒的用户；严禁 purchased、scraped、rented 或第三方名单。

## 最终投递与 hard-bounce suppression

ACS 接受发送 operation 不等于邮件已经进入收件箱。最终状态链路刻意与库存事件 queues 分离：

```text
email worker → ACS 接受确定性 operation ID
                    │
                    └─ ACS recipient delivery report
                         → Event Grid system topic
                         → acs-email-delivery-events queue
                         → airco-alert-delivery-worker
                              ├─ 通过 alertdeliveryindex 关联
                              ├─ 更新 alertdeliveries 最终状态
                              └─ 对 hard failure 更新 alertsuppression
```

Ledger 先记录 `accepted`，再记录 `delivered`、`expanded`、`bounced`、`provider_suppressed`、`quarantined`、`filtered_spam` 或 `provider_failed` 之一；旧的 `sent` 状态继续保持 no-resend 兼容。

三条 queues 的职责被刻意分开：`email-fanout-jobs` 承载 recipient shard 工作，`email-jobs` 承载只含匿名 event/recipient 的投递任务，`acs-email-delivery-events` 承载短期 provider reports。`stock-events` 是 topic，不是第四条 queue。

- Email worker 在发送前把确定性的 ACS message/operation ID 与匿名 event、recipient、delivery ID 绑定，避免快速到达的 Event Grid report 与尚未写入的 correlation row 发生 race。
- Recipient-scoped address fingerprint 把报告绑定到准确的已验证邮箱，但 index 和 suppression table 不保存明文邮箱；因此旧邮箱的 bounce 不能 suppress 用户后来验证的新邮箱。
- `bounced` 与 provider `suppressed` 属于 hard-failure evidence，会启用 system suppression。Email worker 在发送前以及临近实际发送前都检查 suppression。
- 同一 address fingerprint 上更新的 `delivered` report 可以清除 suppression；soft/transient statuses 不会永久 suppress 地址。
- 登录验证码邮件没有 stock-alert correlation binding，因此对应 report 会被忽略。Invalid 或 mismatched report 会 fail closed，并且不得把 recipient payload 写入日志或 DLQ。

## PII 例外与保留期限

正常库存事件的 Service Bus payload 继续保持无 PII。ACS recipient delivery-report schema 必然包含目标邮箱，因此专用最终投递链路是一个被严格限制的例外：

- `acs-email-delivery-events` 是独立 queue，TTL 一天，不得和库存事件、fan-out jobs 或 email jobs 混用。
- 过期的 delivery-report message 不复制到 Service Bus DLQ，因为 DLQ 不会执行 entity TTL。
- 如果处理达到 `maxDeliveryCount`，每日 privacy cleanup job 会删除 delivery-report DLQ 中的 raw messages。Invalid/unbound payload 会直接完成，不把 body 复制到日志或 retry metadata。
- Event Grid delivery dead letters 写入私有 Blob container，并由七天 lifecycle rule 删除，只用于排查 Event Grid → Service Bus 的投递故障。
- `alertdeliveryindex`、`alertdeliveries` 和 `alertsuppression` 只保存匿名 IDs、最终状态与 pseudonymous fingerprints，不保存明文地址；correlation index rows 跟随 90 天 delivery metadata retention，每日 privacy job 会删除 canonical account 已不存在或非 active 的 suppression rows。
- 不要在 Log Analytics 启用 recipient-level ACS `EmailStatusUpdateOperational` logs。应用会自行消费这些 reports，并只记录匿名 delivery IDs；可以保留 ACS send-operation diagnostics 用于排查 quota/request 问题。

任何包含 raw Event Grid body 的调试导出都属于例外 PII 处理：必须加密、限制访问、放在 Git 和 tickets 之外，并在事故解决后立即删除，最迟不能超过七天。

## 监控与响应

必须监控：

- Event Grid `DeadLetteredCount`、`DroppedEventCount` 和连续 delivery-attempt failures。
- `email-fanout-jobs`、`email-jobs` 与 `acs-email-delivery-events` 的 active count、oldest-message age 和 DLQ count，以及 `stock-events/email-fanout` subscription。
- Acceptance → final-status latency，以及 delivered、bounced、provider-suppressed、quarantined、spam-filtered、provider-failed rates。
- 新增 system suppressions、unmatched/invalid reports、scheduled DLQ/privacy cleanup 结果。
- ACS send failures、`429`/quota responses，以及持续运行的 Gmail/Outlook inbox canaries。
- SPF、DKIM 与 DMARC aggregate-report 健康；`support` 或 `dmarc` 的转发故障均视为 incident。

除了已有 Service Bus backlog/dead-letter/throttling/server-error alerts，Foundation 还会覆盖 Event Grid dead-letter、dropped-event 和 repeated delivery-failure metrics。只处理匿名 delivery ID 与归一化状态的 scheduled queries 还会针对 accepted 超过两小时仍无 final report，以及 bounced、provider-suppressed、quarantined、spam-filtered 或 provider-failed 结果发出告警。Alert receiver 必须持续受监控，但地址不得写入文档。提高 sender concurrency 前必须先排除 delivery-report outage，否则 hard bounce 可能继续发生却没有进入 suppression。

## 初始 ACS quota 申请

Higher-quota request 已在 inbound routing、DMARC observation、Reply-To、用户 opt-out、one-click unsubscribe、final-delivery ingestion、hard-bounce suppression、privacy cleanup 和 monitoring 全部部署并完成生产测试后提交。目前仍为 **Open/pending**；提交不等于批准，也不授权提高吞吐。私有 case identifier 应保存在公开仓库之外。

使用如实规划值：

| 字段 | 初始申请内容 |
|---|---|
| Operator | 独立个人运营者；没有注册公司 |
| Service | 面向消费者的订阅服务，追踪欧洲零售商的便携空调库存，提供 transactional stock alerts 与实时库存 dashboard |
| Email type | 用户主动请求的 transactional stock-availability alerts；不发送未经请求的营销邮件 |
| Recipient source | 用户自行注册、验证邮箱、取得付费提醒权益并可自行控制提醒开关；不使用购买、抓取或第三方名单 |
| Initial users | 最多 1,000 |
| 申请的 Portal tier | Tier `250`：provider ceiling 为 1,000 封/分钟、3,000 封/小时 |
| 初始应用上限 | 批准并逐步 warm-up 后最多 100 封/分钟 |
| Hourly volume | 3,000 封/小时 |
| Daily volume | 10,000 封/天 |
| Peak period | 欧洲白天，尤其炎热下午与零售商突发补货时段 |
| Controls | 按库存周期去重、全局 sender throttle、按配送国家过滤、one-click unsubscribe、final-status monitoring 与 hard-bounce suppression |

新域名需要逐步 warm up 两到四周。不能立刻跑满申请到的上限，也不能为了 warm-up 人为制造大批量邮件。在 Azure 批准新 quota 之前，保持当前一个 worker、13 秒发送间隔；获批后才能在持续监控 final delivery 和 complaint signals 的前提下，分阶段调整 `EMAIL_MIN_SEND_INTERVAL_SECONDS` 与 replica limits。

## 生产验证清单

1. `dig MX`、`dig TXT _dmarc`、SPF、DKIM 和 DKIM2 都返回预期 records。
2. 外部真实邮件能分别到达两个 forwarding aliases；用户回复会按 `Reply-To` 到达受监控 support destination。
3. 定向提醒同时含可见链接和两个 RFC 8058 headers；浏览器确认与 one-click POST 都只暂停提醒邮件。
4. Profile 能重新开启提醒，而且不改变付费方案或实时库存权益。
5. 成功 ACS report 能把 `accepted` 更新为 `delivered`；hard-bounce test 会产生 suppression，并阻止同一 address fingerprint 再次发送。
6. 专用 queue 和全部 dead-letter locations 恢复为零；七天 Blob lifecycle 与每日 DLQ cleanup job 均为 enabled。
7. Event Grid、Service Bus、ACS 与 inbox-canary alerts 都能到达 operations receiver。
8. Quota request 已提交但仍在等待审批。Support-case ID 的记录不得包含 contact PII；Azure 明确批准并开始受监控的 warm-up 前，不得提高 sender concurrency。
