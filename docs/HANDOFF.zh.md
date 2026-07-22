# Airco Tracker — 当前交接

<p align="center">
  <a href="./HANDOFF.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HANDOFF-简体中文-d73a49"></a>
  <a href="./HANDOFF.md"><img alt="English" src="https://img.shields.io/badge/HANDOFF-English-0969da"></a>
</p>

最后更新：2026-07-19 UTC

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
- 已部署 backend image/commit：`e6d1f3a6d5c6ee782c4459b0eefe9ed7da3a86d9`
- 已部署的兼容 frontend commit：`e33b3826e5e1c77451688d3a8f738d134e3101a3`
- 最新成功 backend workflow：`29611560636`；最新成功 frontend workflow：`29691574367`
- 最新 foundation deployment：`airco-foundation`（2026-07-17 成功）
- GitHub 加固：两个仓库的 `main` 分支都要求 `validate` status check，并禁止 force-push 和删除；两个部署 workflow 都通过带 required reviewer 的 `production` GitHub environment 门禁。
- GitHub 生产暂停变量：`DEPLOYMENT_PAUSED=false`
- 生产首次发现提醒策略：`ALERT_ON_FIRST_SEEN=true`
- 纯文档 push 不会触发部署 workflow。

## 生产邮件投递与域名信誉控制

邮件投递加固版本已经部署并通过生产验证：

- 已 read back 权威 MX 与两个 monitored forwarding aliases，并通过真实外部 canary 验证。DMARC 只有一条观察模式的 `p=none` 记录，aggregate reporting 由 DNS 服务商管理；forwarding destination 不写入 Git 或支持工单。
- 登录验证和库存提醒邮件都使用自定义域上的结构化 support `Reply-To`。
- 有效 pass 用户可以独立于 pass 权益和实时库存访问开启或暂停提醒邮件。可见退订和 RFC 8058 one-click unsubscribe 使用版本化 HMAC capability，signing key 从 Key Vault 读取。
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

Web/auth 服务为每个用户分配稳定 UUID `userId`；修改邮箱不会改变此 ID。注册、Profile preferences、Stripe 一次性 pass 支付/退款 webhook、pass 撤销和注销账户都会同步 `alertrecipients`。

Projection contract 固定为 32 partitions（`r-00`…`r-1f`），使用 `sha256(userId)` 的最低五位。它只保存提醒所需的最新邮箱、语言、配送国家、`entitlementTier`、`entitlementStatus`、`entitlementExpiresAt`、enabled 和同步 metadata。`alerts` 与 `radar` 都可接收邮件，只有 `radar` 在 Web 服务中获得实时库存访问。迁移期后端仍兼容读取旧 recurring-subscription 字段，但新 pass 字段为权威。改变 shard count 必须在两个仓库进行协调、版本化迁移。

Backend reconciler 支持旧 rows 的确定性 UUID 回填、记录用于常数时间权威投递读取的私有 canonical source-row pointer，并使用安全/乐观并发删除规则。它是每日 repair path，不会让每个库存事件依赖完整 `users` 扫描。只有在旧 source row 重新派生出的 UUID 与请求的 recipient UUID 完全一致时才会信任该 row。

## 四语投递契约

- 用户语言支持 `zh`、`nl`、`en`、`fr`，从 canonical Profile 经 `alertrecipients` 一直传到 email worker 发信前的权威重读。
- 库存提醒的 subject、导语、HTML 标题、价格、配送国家、footer 和可见退订链接均本地化。英/荷/法正确区分单复数，法语价格使用法式分隔符。可见退订 URL 保留收件人语言；RFC 8058 one-click API URL 继续保持语言无关。
- `airco_tracker/i18n_local.json` 是 `email` 和 `web` 两个 Table partition 的完整播种源，每个 key 都恰好有四个非空值。`web` map 与前端 fixture 按值完全同步；生产发布必须在发布前或发布过程中 upsert，且后端 loader 按进程缓存，因此新进程需要在更新后加载。
- 商家名、商品名和商家原始配送说明作为来源证据保留原文，不做机器翻译。

## 安全和隐私

- 生产使用 Entra ID/OAuth 和 user-assigned Managed Identity。Service Bus 和 ACS 禁用 local authentication；Storage 默认 OAuth，Blob container 保持私有。
- Scanner/shared web runtime、publisher、fan-out、email delivery 和 delivery-report processing 使用相互分离的身份；流水线权限在 Azure RBAC 支持的范围内限制到具体 entity/table。本次待部署加固还把 vault-wide secret access 换成精确 secret scope：Web 仅能读取 unsubscribe/withdrawal/OTP pepper，scanner 仅能读取 Awin/AliExpress 凭据，email 仅能读取 unsubscribe key。只有 Event Grid 因 Azure managed-identity dead-letter validation 要求保留 storage-account scope Blob role；delivery publisher 仅拥有 `alertdeliveries` 表级读取权限。GitHub 通过 OIDC 和 least-privilege custom role 部署，不能创建 role assignments 或读取应用 secrets。`infra/github-oidc.bicep` 会在 `airco-github-deployer` 上同时创建 ref-scoped 和 environment-scoped 的 federated credentials（`github-airco-tracking-env-production`、`github-airco-tracking-web-env-production`），因为声明 `environment:` 的 job 会拿到 environment-scoped 的 token subject。
- 旧的 storage-account 级 `Storage Table Data Contributor` 已移除，shared identity 的 blob data-plane 权限也已收窄到 `airco-tracker` container。新的 custom role `aircontrack-acs-email-sender`（三个 action，依据 Microsoft Learn SMTP authentication 指南）已取代 `aircontrack-identity` 和 `aircontrack-alert-email` 的 `Communication and Email Service Owner`。旧的宽泛 assignment 已在验证后手工删除。Key Vault `AuditEvent` 诊断流向 operations Log Analytics workspace；每月 €50 的 cost budget 在实际花费 80%/100% 时通知 operations action group。Key Vault soft-delete 保留期仍为 7 天：Azure 在 vault 创建时固定该值且拒绝修改（template 注释有说明）。
- 普通应用 queue messages 不含邮箱、昵称、Stripe/customer/payment IDs、卡片数据或私有 canonical source-row pointer。专用 provider-report queue 是范围严格受限的例外，因为 ACS delivery event 必然包含 recipient；一日 TTL、私有 dead letter 与 cleanup policy 限制了该暴露。`alertdeliveries`、`alertdeliveryindex` 和 suppression rows 只保留 opaque IDs/fingerprints 与归一化 status。
- 除上述受限 provider-report 路径外，邮箱只存在 canonical `users` 和最小化的 `alertrecipients` projection。Email worker 发信前实时解析，日志只输出遮蔽形式。
- 生产没有 `EMAIL_TO`/`notification-email` fallback。无法读取当前权益或地址时必须 fail closed。
- Key Vault 保存少量必要的应用与 adapter secrets，其中包括 unsubscribe signing key。Secret value 不得进入 Git、镜像、Bicep parameters、Service Bus payload、日志或浏览器代码。
- 镜像通过 `requirements.lock`（uv 生成、带 hash）以 `pip install --require-hashes` 安装依赖，base image 按 digest 固定，urllib3 ≥2.7（清除五个 PYSEC advisory），并要求 `requires-python >=3.12`；pip-audit 在 CI 和 deploy 中均为 blocking。

## 扩展性和当前 quota 限制

Scanner 工作量不随 subscriber 数量变化。Recipient expansion 独立扩缩容，并按 32 个 Table partitions 分页流式读取。Canonical `users` 只由每日 reconciler 流式扫描；email worker 每次实际投递只做一次权威 point read（UUID row，或 reconciler 记录的旧 source row）。只有尚未回填的旧 projection 才使用有界兼容 query。因此当前 hot path 不需要手工分表。

Coordinator 最多 4 replicas，fan-out 最多 16。Service Bus Standard entities 使用 batching 和确定性 duplicate detection。调整拓扑前要先监控 backlog age、active/dead-letter counts、throttling、pending outbox age、delivery failures 和 ACS `429`。

生产使用已验证的 customer-managed ACS sender domain `airco-tracker.eu`；Domain、SPF、DKIM、DKIM2 均已验证，连接时保留了 `AzureManagedDomain` 作为回滚路径，两个应用也通过 `ACS_EMAIL_DOMAIN_NAME` 明确选择了该域名。官方文档中的默认自定义域配额是每分钟 30 封、每小时 100 封。Delivery failure、bounce、suppression、unsubscribe 和 complaint observation controls 已投入运行，tier-250 quota request 处于 Open；在 Azure 批准前，email app 继续限制为一个 replica、两次发送至少间隔 13 秒。批准前提高 worker 数量是不安全的。

## 库存和 retailer 语义

- 当前有 46 个无需凭据的 active adapters：荷兰 28 个、法国 18 个；Costway France 因持续 HTTP 403 已从 active registry 移除，代码保留为 deferred adapter。README 是 active list 和 per-retailer notes 的权威记录。
- 只追踪真实压缩机空调。排除 air coolers、风扇、配件、quote-only items、不在受支持 portable scope 内的 fixed split、仅门店/仅自取、过期 deals 和多周交期。
- 预售可以进入 dashboard，但不能触发现货邮件；预售转即时现货是有效 restock transition。Alert 和 inventory 两条路径共用同一个预售检测 `with_detected_presale`（`adapters/base.py`）；Klimaatshop/EP.nl 的多周交期会归类为预售，不再触发即时现货邮件。
- EcoFlow France 读取法国官方 Shopify 目录和商品数据。Shopify variant availability 始终是权威依据，预售文案只用于把已可下单 variant 分类为预售；当前商品链接直接指向 EcoFlow France 官方商品页。
- E.Leclerc France 通过商家官方店面 live API 发现商品并在每轮扫描确定库存，用户跳转则经过 Awin deep link，advertiser 为 `15135`、publisher 为 `2981827`。即时现货和预售严格分离，预售绝不会触发即时现货提醒。
- Trotec France 的官方店面 Algolia 是即时库存和预售的唯一权威。`sold_out` 只接受严格的已知 boolean/string；可下单状态遇到缺失或未知 veto signal 时会 fail closed。已批准的 Awin advertiser `62319` 只在第一方分类完成后通过 Link Builder Batch API 使用。API 链接缓存一天，并且必须同时匹配经过验证的 Trotec canonical URL、advertiser `62319` 与 publisher `2981827`；请求、单条结果或缓存校验失败都会回退 canonical URL，不能让库存 stale。`Product.url` 继续作为状态、去重和事件身份。Bearer token 从 Key Vault secret `awin-publisher-api-token` 加载到 `AWIN_PUBLISHER_API_TOKEN`；带 secret URL 的 Legacy feed 不受支持。在真正接入 CMP 前，所有返回链接强制使用 `cons=0`，这会关闭 Awin cookie 和 click identifier（因此当前不能依赖它完成佣金归因）。网页和邮件都必须在点击前显示推广联盟披露。
- GAMMA 和 KARWEI 正常解析分类商品卡；Azure 访问该分类 host 时收到 Vercel 429，因此生产 fallback 使用店面公开的只读目录，并以多个线上库存字段相互一致作为现货条件。Robots 声明的 sitemap 只能确认当前商品目录安全为空，sitemap 收录本身绝不代表现货。Schema/key/index 或 sitemap 漂移时会 fail closed 并保留 stale inventory。
- Action France fail closed，因为其搜索结果页卡片无法验证线上可下单性。Action NL 会否决过期 deal（`deal verlopen`）。Costway NL 在数量标记缺失时 fail closed，并检测页面级 markup drift。Hubo 的现货标记只匹配商品区块。
- Lidl、Hubo、FlinQ 和 Airco-Webwinkel 的季节性空目录现在按合法的零结果处理，而不是标记为 stale inventory。
- 单一 retailer 失败不能阻断其它站点。失败站点保留上次成功库存并标记 `status: error` / `stale: true`；alert state 只为成功站点更新。
- Live inventory 与 alert state 分离。Inventory schema version `1` 是生产跨仓库契约；breaking change 必须显式 bump version 并协调前后端 release。
- Direct 403/anti-bot candidates 记录在 [RETAILER_403_BACKLOG.zh.md](./RETAILER_403_BACKLOG.zh.md)。不得绕过 CAPTCHA、robots restrictions、login walls 或 anti-bot controls。

## 外部 API 状态

- Conrad storefront 被 Cloudflare 阻挡。只有 allowlist/approval 后才能使用官方 Price & Availability API；不得恢复反爬 scraping。
- AliExpress affiliate access 已批准，但实现前仍需重新确认 Open Platform application/key/官方 signing 状态。只读取 catalog/affiliate scopes，不收集 buyer、order、payment 或其它个人数据。

## 待部署的加固版本（尚未完成生产验证）

当前 worktree 已拆分 Web、scanner 和 retention identities；把 `alertoutboxpending` 改为保存完整 payload 的权威 enqueue journal，并提供覆盖全部 continuation pages 的旧数据迁移和崩溃安全 archive 修复；从所有实时汇总排除 stale retailer 商品，并在 24 小时诊断窗口后清空其商品 payload；让所有零售商及生产 partner API 流量统一经过有界且校验 redirect 的 fetch 边界，并为各 endpoint 配置 MIME/大小限制和显式只读 POST 重试；校验 canonical 与 affiliate URL hosts；90 天后压缩缺货商品状态、365 天后删除 tombstone；并移除 retention 固定 5,000-row 上限。CI 现在会使用 hash-locked dependency set 验证 push `main`。

Owner 部署 foundation/application 并验证四个 workload identity bindings、Web/inventory/email smoke tests 后，才显式运行 `scripts/migrate-runtime-identities.sh --apply`。该脚本默认 dry-run，会在删除前验证所有精确替代 grants，只删除枚举出的 legacy grants（包括三个 vault-wide secret-reader grants），并再次验证结果。GitHub 被刻意限制为不能执行这项 RBAC cleanup。完整契约、回滚和幂等说明见 [HARDENING.zh.md](./HARDENING.zh.md)。

## 本次 release 已完成验证

Backend image/commit `e6d1f3a6d5c6ee782c4459b0eefe9ed7da3a86d9` 已部署并完成生产验证。Workflow `29611560636` 通过新的 `production` GitHub environment 门禁人工批准后部署。首次带门禁的 run `29610815334` 在 OIDC 登录失败，因为声明 `environment:` 的 job 会获得 environment-scoped 的 token subject；为 `airco-github-deployer` 身份添加 federated credentials `github-airco-tracking-env-production` 和 `github-airco-tracking-web-env-production` 后修复。

- Backend：此前部署版本通过 355/355 unit tests。待部署加固 worktree 已通过完整 unit test suite、`compileall`、shell syntax、全部 Bicep builds 和 `git diff --check`，但尚未部署到生产。上次真实 dry-run 覆盖 46/46 retailer、零失败，alert 与 inventory 路径的预售判定一致。
- Frontend：workflow `29691574367`（同样经 `production` environment 批准）部署 commit `e33b3826e5e1c77451688d3a8f738d134e3101a3`；生产服务 revision 为 `airco-tracking-web--0000065`。
- 手工 scanner 执行 `airco-tracker-job-a822jhn` 在旧的宽泛 RBAC grant 删除之后成功，证明 container 级 blob 权限加 custom ACS sender role 已足够。
- 部署验证执行 reconciler（suffix `o7b9y3p`）和 scanner（suffix `mu9osx3`）均成功；publisher 每分钟运行；三条 Service Bus queue 最终均为 0 active / 0 DLQ。
- Foundation 重新部署 `airco-foundation` 成功，包含上文所述的 RBAC 收窄、Key Vault 诊断和 cost budget。Owner 的 CLI 身份没有任何 blob data-plane 权限，least privilege 得到确认。
- 生产 Table `i18n` 已重新播种为 `email` 与 `web` 两个 scope 共 64 条，新增 `legal_privacy_link`、`legal_terms_link`、`legal_imprint_link` 和 `legal_affiliate_link`，并把 `checking_subscription` 同步为 Heatwave Pass 文案。播种所用的临时 user-level table grant 已在其后撤销。

## 部署顺序

Foundation/RBAC 必须由 Owner 或同等具有 role-assignment 权限的本地 principal 运行；GitHub deployer 被刻意限制为无法创建 RBAC：

```bash
cd ~/airco-tracking
az login
./scripts/deploy-azure.sh
./scripts/bootstrap-github-oidc.sh
```

普通 application release 由 push `main` 触发：测试并构建 SHA-tagged immutable image；已有环境会先用候选镜像运行 reconciler canary，成功后才更新 jobs/apps 并验证 reconciler → scanner → publisher。部署或验证失败会自动重新应用已记录的 previous image。对应前端部署会让上一 revision 在候选 direct smoke/readiness 验证期间继续承载 100% traffic，失败时自动恢复上一 revision traffic。这些控制仍属于待部署验证状态。新 RBAC 尚未传播时应等待后重跑，不能扩大权限绕过。

## 下一步

1. 继续监控 ACS quota case `06bfd9d3-65c22af0-6d841855-b8dc-4aea-8d93-d2364a875032`（仍为 **Open**），并用现有 consent、authentication、unsubscribe、final-delivery、suppression、monitoring 和 warm-up 证据回复 Azure。获批前保持一 worker 和 13 秒间隔，批准后在 100 封/分钟、10,000 封/天的上线限制内保守提高并发。
2. 用两到四周 warm up 自定义域，然后在审查 aggregate reports、provider complaints、bounce/suppression rate 和 inbox placement 的同时，把 DMARC 从观察模式 `p=none` 收紧到 `quarantine`、最终 `reject`；失败率保持在 1% 以下。
3. 确认一封真实 OTP 登录邮件到达——这是新 custom role `aircontrack-acs-email-sender` 下的首次发送。
4. 填写 legal 页面的 `[TODO]` 占位（运营主体、VAT、退款、管辖法律），并在退出 Stripe test mode 前完成法律审查。
5. 中期事项：E.Leclerc 商品 URL identity 迁移、BTU enrichment cache、pagination 覆盖、PaaS public endpoint 收紧或书面风险接受、GAMMA/KARWEI sanctioned feed 或书面许可（任何契约失败都继续 fail closed）。

## 更新本 handoff

替换过期状态，不追加流水账。记录准确的 deployed commit/image、workflow/execution IDs、verification counts、剩余 blocker、frontend contract compatibility 和下一项行动。中英文文件必须同步，且不能包含 PII/secrets。
