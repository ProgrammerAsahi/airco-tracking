# Airco Tracker — 当前交接

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

最后更新：2026-07-15 CEST（Europe/Amsterdam）

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
- Reconciler job：`airco-alert-reconciler-job`，`17 3 * * *` UTC
- Production mail provider：Azure Communication Services Email
- 已部署 backend image/commit：`efeb50220e7b1d4a6a607f84d65038af620b3feb`
- 已部署的兼容 frontend commit：`db98ce83f7f46517a75fa9977d4985dc25d5eee1`
- 最新成功 backend workflow：`29372369189`；最新成功 frontend workflow：`29367033016`
- 最新 foundation deployment：`airco-foundation`（2026-07-11 成功）
- GitHub 生产暂停变量：`DEPLOYMENT_PAUSED=false`
- 生产首次发现提醒策略：`ALERT_ON_FIRST_SEEN=true`
- 纯文档 push 不会触发部署 workflow。

## 生产邮件投递与域名信誉控制

邮件投递加固版本已经部署并通过生产验证：

- 已 read back 权威 MX 与两个 monitored forwarding aliases，并通过真实外部 canary 验证。DMARC 只有一条观察模式的 `p=none` 记录，aggregate reporting 由 DNS 服务商管理；forwarding destination 不写入 Git 或支持工单。
- 登录验证和库存提醒邮件都使用自定义域上的结构化 support `Reply-To`。
- 付费用户可以独立于 billing 和实时库存权益开启或暂停提醒邮件。可见退订和 RFC 8058 one-click unsubscribe 使用版本化 HMAC capability，signing key 从 Key Vault 读取。
- ACS recipient-level final delivery 通过 Event Grid → 专用 Service Bus queue → 独立 delivery-report worker。Ledger 保存归一化终态，hard bounce 只 suppress 对应地址，no-resend 检查保持权威。
- Raw recipient data 被限制在 provider-report 路径：一日 queue TTL、过期不进 DLQ、私有 Event Grid dead letters 七日 lifecycle，以及每日 Service Bus delivery-report DLQ privacy-cleanup job。
- Event Grid dead-letter/dropped/repeated-failure alerts、privacy-safe final-outcome queries 和 ACS operation diagnostics 均已启用；首次手动 cleanup execution 与真实 Action Group 通知都成功。

ACS higher-quota case `06bfd9d3-65c22af0-6d841855-b8dc-4aea-8d93-d2364a875032` 当前为 **Open**。Portal 申请档位是 `250`（1,000 封/分钟、3,000 封/小时），但应用初期会自行限制为最多 100 封/分钟、10,000 封/天，面向初期最多 1,000 用户。在 Azure 批准前继续保持已部署的一 worker/13 秒限制；批准后用两到四周逐步 warm up。

## 异步提醒流水线

完整设计见 [ALERT_PIPELINE.zh.md](./ALERT_PIPELINE.zh.md)；consent、domain reputation、final-delivery、suppression、retention 与 quota 流程见 [EMAIL_DELIVERY.zh.md](./EMAIL_DELIVERY.zh.md)。已部署的生产流程：

1. Scanner 持有分布式 lease，更新 Blob snapshot/state，并在推进 alert state 前把确定性的 `stock.available.v1` 事件持久化到 `alertoutbox`。
2. `airco-alert-publisher-job` 每分钟把 pending rows 发布到 `stock-events` topic。
3. `airco-alert-fanout-coordinator` 消费 `email-fanout` subscription，在 `email-fanout-jobs` 中创建 32 个 shard jobs。
4. 最多 16 个 fan-out workers 流式读取相应 `alertrecipients` partition，并向 `email-jobs` 写入只含匿名 recipient ID 的 jobs。
5. Email worker 重新读取最新 recipient、再次检查权益/国家/event age、以 ETag 保护的方式认领 `alertdeliveries` row，再用确定性 operation ID 通过 ACS 发送。
6. ACS final-delivery events 通过 Event Grid system topic `aircontrack-acs-email-events` 和 `acs-email-delivery-events`；delivery worker 归一化结果、更新 `alertdeliveries`/`alertdeliveryindex`，并执行 address-specific hard-bounce suppression。

辅助 schedules：

- `airco-alert-reconciler-job`：每天 `03:17 UTC`（`17 3 * * *`），从 canonical `users` 修复由 Web 维护的投影。
- `airco-alert-retention-job`：每天 `02:17 UTC`，30 天后删除已发布 outbox rows，90 天后删除终态 delivery rows。
- `airco-delivery-dlq-cleanup`：每天 `02:43 UTC`，清除专用 delivery-report DLQ 中的 raw provider reports。

Service Bus stock/fan-out 消息 TTL 一天；email jobs 和应用 event freshness 为六小时。Delivery-report 消息 TTL 一天、`maxDeliveryCount=16`，过期 payload 不复制到 DLQ；duplicate detection 为七天。无效/永久失败的应用消息进入 dead letter，而不是被静默完成。

## 跨仓库 recipient contract

Web/auth 改动为每个用户加入稳定 UUID `userId`；修改邮箱不会改变此 ID。注册、Profile preferences、Stripe subscription webhooks、取消订阅和注销账户都会同步 `alertrecipients`。

Projection contract 固定为 32 partitions（`r-00`…`r-1f`），使用 `sha256(userId)` 的最低五位。它只保存提醒所需的最新邮箱、语言、配送国家、plan/status/period end、enabled 和同步 metadata。改变 shard count 必须在两个仓库进行协调、版本化迁移。

Backend reconciler 支持旧 rows 的确定性 UUID 回填、记录用于常数时间权威投递读取的私有 canonical source-row pointer，并使用安全/乐观并发删除规则。它是每日 repair path，不会让每个库存事件依赖完整 `users` 扫描。只有在旧 source row 重新派生出的 UUID 与请求的 recipient UUID 完全一致时才会信任该 row。

## 四语投递契约

- 用户语言支持 `zh`、`nl`、`en`、`fr`，从 canonical Profile 经 `alertrecipients` 一直传到 email worker 发信前的权威重读。
- 库存提醒的 subject、导语、HTML 标题、价格、配送国家、footer 和可见退订链接均本地化。英/荷/法正确区分单复数，法语价格使用法式分隔符。可见退订 URL 保留收件人语言；RFC 8058 one-click API URL 继续保持语言无关。
- `airco_tracker/i18n_local.json` 是 `email` 和 `web` 两个 Table partition 的完整播种源，每个 key 都恰好有四个非空值。`web` map 与前端 fixture 按值完全同步；生产发布必须在发布前或发布过程中 upsert，且后端 loader 按进程缓存，因此新进程需要在更新后加载。
- 商家名、商品名和商家原始配送说明作为来源证据保留原文，不做机器翻译。

## 安全和隐私

- 生产使用 Entra ID/OAuth 和 user-assigned Managed Identity。Service Bus 和 ACS 禁用 local authentication；Storage 默认 OAuth，Blob container 保持私有。
- Scanner/shared web runtime、publisher、fan-out、email delivery 和 delivery-report processing 使用相互分离的身份；流水线权限在 Azure RBAC 支持的范围内限制到具体 entity/table。只有 Event Grid 因 Azure managed-identity dead-letter validation 要求保留 storage-account scope Blob role；delivery publisher 仅拥有 `alertdeliveries` 表级读取权限。GitHub 通过 OIDC 和 least-privilege custom role 部署，不能创建 role assignments 或读取应用 secrets。
- 旧的 storage-account 级 `Storage Table Data Contributor` 已移除。Shared runtime 只保留所需的逐表 contributor/reader 和 Blob 权限；移除后生产 OTP、Profile/投影写入、登出、retention 和 scanner execution 全部通过。
- 普通应用 queue messages 不含邮箱、昵称、Stripe/customer/payment IDs、卡片数据或私有 canonical source-row pointer。专用 provider-report queue 是范围严格受限的例外，因为 ACS delivery event 必然包含 recipient；一日 TTL、私有 dead letter 与 cleanup policy 限制了该暴露。`alertdeliveries`、`alertdeliveryindex` 和 suppression rows 只保留 opaque IDs/fingerprints 与归一化 status。
- 除上述受限 provider-report 路径外，邮箱只存在 canonical `users` 和最小化的 `alertrecipients` projection。Email worker 发信前实时解析，日志只输出遮蔽形式。
- 生产没有 `EMAIL_TO`/`notification-email` fallback。无法读取当前权益或地址时必须 fail closed。
- Key Vault 保存少量必要的应用与 adapter secrets，其中包括 unsubscribe signing key。Secret value 不得进入 Git、镜像、Bicep parameters、Service Bus payload、日志或浏览器代码。

## 扩展性和当前 quota 限制

Scanner 工作量不随 subscriber 数量变化。Recipient expansion 独立扩缩容，并按 32 个 Table partitions 分页流式读取。Canonical `users` 只由每日 reconciler 流式扫描；email worker 每次实际投递只做一次权威 point read（UUID row，或 reconciler 记录的旧 source row）。只有尚未回填的旧 projection 才使用有界兼容 query。因此当前 hot path 不需要手工分表。

Coordinator 最多 4 replicas，fan-out 最多 16。Service Bus Standard entities 使用 batching 和确定性 duplicate detection。调整拓扑前要先监控 backlog age、active/dead-letter counts、throttling、pending outbox age、delivery failures 和 ACS `429`。

生产使用已验证的 customer-managed ACS sender domain `airco-tracker.eu`；Domain、SPF、DKIM、DKIM2 均已验证，连接时保留了 `AzureManagedDomain` 作为回滚路径，两个应用也通过 `ACS_EMAIL_DOMAIN_NAME` 明确选择了该域名。官方文档中的默认自定义域配额是每分钟 30 封、每小时 100 封。Delivery failure、bounce、suppression、unsubscribe 和 complaint observation controls 已投入运行，tier-250 quota request 处于 Open；在 Azure 批准前，email app 继续限制为一个 replica、两次发送至少间隔 13 秒。批准前提高 worker 数量是不安全的。

## 库存和 retailer 语义

- 当前有 47 个无需凭据的 active adapters：荷兰 28 个、法国 19 个。README 是 active list 和 per-retailer notes 的权威记录。
- 只追踪真实压缩机空调。排除 air coolers、风扇、配件、quote-only items、不在受支持 portable scope 内的 fixed split、仅门店/仅自取、过期 deals 和多周交期。
- 预售可以进入 dashboard，但不能触发现货邮件；预售转即时现货是有效 restock transition。
- EcoFlow France 读取法国官方 Shopify 目录和商品数据。Shopify variant availability 始终是权威依据，预售文案只用于把已可下单 variant 分类为预售；当前商品链接直接指向 EcoFlow France 官方商品页。
- E.Leclerc France 通过商家官方店面 live API 发现商品并在每轮扫描确定库存，用户跳转则经过 Awin deep link，advertiser 为 `15135`、publisher 为 `2981827`。即时现货和预售严格分离，预售绝不会触发即时现货提醒。
- GAMMA 和 KARWEI 正常解析分类商品卡；Azure 访问该分类 host 时收到 Vercel 429，因此生产 fallback 使用店面公开的只读目录，并以多个线上库存字段相互一致作为现货条件。Robots 声明的 sitemap 只能确认当前商品目录安全为空，sitemap 收录本身绝不代表现货。Schema/key/index 或 sitemap 漂移时会 fail closed 并保留 stale inventory。
- 单一 retailer 失败不能阻断其它站点。失败站点保留上次成功库存并标记 `status: error` / `stale: true`；alert state 只为成功站点更新。
- Live inventory 与 alert state 分离。Inventory schema version `1` 是生产跨仓库契约；breaking change 必须显式 bump version 并协调前后端 release。
- Direct 403/anti-bot candidates 记录在 [RETAILER_403_BACKLOG.zh.md](./RETAILER_403_BACKLOG.zh.md)。不得绕过 CAPTCHA、robots restrictions、login walls 或 anti-bot controls。

## 外部 API 状态

- Conrad storefront 被 Cloudflare 阻挡。只有 allowlist/approval 后才能使用官方 Price & Availability API；不得恢复反爬 scraping。
- AliExpress affiliate access 已批准，但实现前仍需重新确认 Open Platform application/key/官方 signing 状态。只读取 catalog/affiliate scopes，不收集 buyer、order、payment 或其它个人数据。

## 本次 release 已完成验证

Backend image/commit `efeb50220e7b1d4a6a607f84d65038af620b3feb` 已部署并完成生产验证。它通过 239/239 后端 tests、翻译完整性和前端 map 一致性检查、JSON parsing、`compileall` 与 `git diff --check`。Unit coverage 包含法语 Profile 投影、worker 重读、法国/荷兰配送文案、法/荷金额格式、邮件 HTML/纯文本单复数、法语退订导航，以及 EcoFlow/E.Leclerc 即时现货与预售的严格判定。

- Backend：239/239 unit tests、compileall、shell syntax、两个 Bicep entry points、`git diff --check` 和真实 retailer 解析全部通过。
- Frontend：71/71 tests、app/server typecheck、production build、Bicep/deployment verification 和生产 HTTP checks 全部通过。法语 Landing、Subscribe、Profile、登录和退订状态已通过桌面与窄屏视觉检查；生产浏览器 console 没有 warning 或 error。
- GitHub workflow `29371474576` 执行了静默暂停部署。播种执行 `airco-tracker-job-ig2bh32` 成功，日志为 `No new stock` / `no outbox`，避免两个新启用 retailer 产生首次发现邮件。
- 恢复 workflow `29372369189` 成功；其验证执行中 reconciler suffix 为 `vi4r23m`、scanner suffix 为 `1cpvmx0`、publisher suffix 为 `czszdod`，三者全部成功。最终生产设置为 `DEPLOYMENT_PAUSED=false` 和 `ALERT_ON_FIRST_SEEN=true`。
- GitHub 继续通过 workflow `29367033016` 服务兼容的 frontend SHA `db98ce8…`；Frontend image 在 revision `airco-tracking-web--0000053` 承接 100% 流量。
- 生产 Table `i18n` 已播种 `email` 与 `web` 两个 scope、共 56 条。38-key web map 的四种语言均非空，并与前端 fallback 按值完全一致；播种/支持检查所用的临时表级权限已撤销，复查 assignment 数量为 0。
- Event Grid system topic/subscription、`email-fanout` subscription、三条 queues、两张 delivery tables、七日 dead-letter lifecycle、七条 metric alerts 和两条 scheduled-query alerts 均已启用。最终检查的四个 broker entities 的 active、scheduled、transfer-DLQ 和 DLQ 全部为零。
- Customer-managed ACS domain 状态为 `Succeeded`；Domain/SPF/DKIM/DKIM2 均为 `Verified`，已连接到 Communication Service，同时保留 `AzureManagedDomain` fallback。生产发件身份为 `Airco Tracker <DoNotReply@airco-tracker.eu>`。
- 生产定向事件 `a4ec09309cd8fa12ba09881f27ea635d5a05baa7420654495ffce4fc024b5ead` 对两个已授权 recipient 都到达最终 `delivered`，两个 monitored provider 都将其放入收件箱。Gmail 原始邮件头显示对齐的 SPF、DKIM、DMARC 全部通过，Reply-To 正确，包含 HTTPS `List-Unsubscribe`，并具有精确的 RFC 8058 `List-Unsubscribe-Post` 语义；此处不记录地址。
- 一封生产法语 OTP 已由自定义域 sender 投递到已授权 Outlook inbox，subject、标题、有效期和安全提示均为法语。随后法语 canary event `a78f237c1ae49be79519c4049c11f4876864ae224b5b77f630cfe9cbb3ed33df` 对已授权 Gmail 测试账户到达最终 `delivered`；subject、正文和可见暂停提醒链接均为法语。测试后 Profile 偏好已恢复，topic subscription 与全部三条 queues 的 active/DLQ 均回到 0。
- 真实 one-click POST 在无需登录的情况下暂停提醒邮件，付费订阅和库存权益保持不变。重新开启提醒会轮换 capability；旧链接仍幂等返回，但不能改变新状态。
- 真实外部 inbound-forwarding canary 到达两个 monitored mailbox。一次初始 support-forwarding canary 进入 spam，因此仍需逐步 warm-up 和 reputation monitoring。DMARC 在 aggregate reports 和合法 sender 完成审查前保持观察模式 `p=none`。
- 生产验证返回 47 个站点、92 个可用商品，其中 1 个站点 stale。EcoFlow France 返回 16 个商品 / 2 个可用；E.Leclerc France 返回 18 个商品 / 16 个可用。Costway France 返回 HTTP 403，并按设计保留上次结果为 stale，没有伪造或清空库存。
- 恢复后第一个自然 cron `airco-tracker-job-29734470` 也执行成功。EcoFlow 和 E.Leclerc 仍分别稳定在 16/2 与 18/16；另一家 De'Longhi 的 PACEM90K.1SILENT 出现了经实测的即时现货转换，使快照增至 93 个可用商品。Publisher 只发布了这 1 个事件，随后 outbox 恢复为空，证明两个新适配器没有产生首次发现噪音。
- 恢复和验证完成后，publisher outbox 为空。GAMMA/KARWEI 在分类 host 对 Azure 限流时继续使用严格的 public-catalogue fallback；schema/key/index 漂移仍会 fail closed，不能制造库存。
- `/`、`/health` 和 `www` health 返回 200；匿名 `/api/inventory` 按要求返回 401。Monitoring Action Group 已发送真实 inbox 通知，首次 delivery-DLQ cleanup execution 也成功。

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

1. 监控 quota case `06bfd9d3-65c22af0-6d841855-b8dc-4aea-8d93-d2364a875032`，并用现有 consent、authentication、unsubscribe、final-delivery、suppression、monitoring 和 warm-up 证据回复 Azure；在 Azure 确认前不得描述为已批准。
2. 获批前保持一 worker 和 13 秒间隔。批准后保守提高并发，保留应用层 100 封/分钟与 10,000 封/天的上线限制，并在负载下验证 backlog、ACS `429`、final outcomes 和 suppression。
3. 用两到四周 warm up 自定义域，同时审查 DMARC aggregates、provider complaints、bounce/suppression rate、adverse final outcomes 与 inbox placement，并把失败率保持在 1% 以下。
4. 继续监控 GAMMA/KARWEI 公开目录的 key/index/schema，并为长期使用争取 sanctioned feed 或书面许可；任何契约失败都继续 fail closed，不能制造错误现货。

## 更新本 handoff

替换过期状态，不追加流水账。记录准确的 deployed commit/image、workflow/execution IDs、verification counts、剩余 blocker、frontend contract compatibility 和下一项行动。中英文文件必须同步，且不能包含 PII/secrets。
