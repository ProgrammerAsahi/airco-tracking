# Airco Tracker — 运行时加固与迁移

<p align="center">
  <a href="./HARDENING.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/HARDENING-简体中文-d73a49"></a>
  <a href="./HARDENING.md"><img alt="English" src="https://img.shields.io/badge/HARDENING-English-0969da"></a>
</p>

本文记录 2026 年 7 月加固版本新增的库存 freshness 契约、出站 URL 边界、有界抓取、状态/提醒保留、pending outbox 索引，以及只能由 Owner 执行的运行身份迁移。它属于部署前文档；只有完成下述部署和验证后，才能把这些改动写成“生产已验证”。

## 库存 freshness 契约

Inventory schema 继续保持版本 `1`，新增字段全部为 additive。根级汇总只统计最近一次扫描成功的网站。每个 site 新增 `freshness`（`verified` 或 `stale`）、`counts_toward_totals`、`stale_age_seconds` 和 `stale_too_old`。根级新增 `verified_site_count`、`inventory_confidence`（`verified`、`partial` 或 `unavailable`）及 `stale_diagnostic_max_age_seconds`（86,400 秒）。

零售商扫描失败时，只把上次成功商品短期保留为诊断证据，绝不能增加实时库存、即时现货或预售汇总。当 `stale_too_old=true`（超过 24 小时，或不存在可信的成功时间）时，生产端会清空商品列表，只保留站点健康状态和时间戳。消费端不得把 stale row 当作可购买库存。

## URL 与抓取边界

所有持久化 canonical 商品 URL 必须使用 HTTPS，并匹配相应国家/site 的显式商家 host 集合。推广链接只允许批准的 Awin/AliExpress redirect hosts。带 user-info、控制字符、fragment、超长 URL、非 443 端口、未知真实商家或 event 中不匹配的 URL 均 fail closed。新增或更名 adapter 时，必须在同一改动中更新 `MERCHANT_HOSTS_BY_SITE_ID` 及覆盖测试。

共享 fetcher 禁用自动跳转，最多跟随三次 redirect。默认只允许与源请求完全相同的 hostname 及其严格对应的 `www`/裸域；任何 sibling subdomain 都必须由该调用显式放行，绝不会根据 public-suffix 的末尾标签推断同站。每次调用都声明 MIME allow-list，并把所有 body 流式读入该调用自己的最大字节数。普通 HTML 继续使用 10 KiB 的 anti-bot shell 下限；紧凑但合法的 JSON/XML endpoint 会显式使用更小下限，因此可接受合法的 `{"products": []}` 或小型 sitemap，而不会削弱 HTML 检查。即使目标 host 已被允许，敏感 header 也绝不会跨 host 转发。

所有零售商页面、sitemap、Algolia/Nosto/Shopify endpoint、E.Leclerc API、生产 Awin link-builder 与生产 AliExpress 请求都经过该边界。`POST` 默认只请求一次；只有在代码中明确说明、逻辑上只读的 catalogue query 才会 opt-in 两次有界重试，mutation 绝不能开启该选项。`tests/test_adapter_transport_boundary.py` 会静态阻止 adapter 导入 HTTP client、直接访问 `.session` 或直接调用 `.post`，并由 fetcher 聚焦测试覆盖 redirect、MIME、大小与 retry 行为。

## 有界状态与数据保留

缺货商品完整诊断状态保留 90 天，之后压缩成最小 tombstone，365 天后删除。压缩会保留 availability generation，因此短期内重新出现仍能生成正确的补货 transition。周期由 `STATE_COMPACT_AFTER_DAYS` 和 `STATE_TOMBSTONE_RETENTION_DAYS` 配置。

每日 retention job 默认没有固定行数上限，会耗尽 Azure Table continuation pages。生产 runtime budget 为 240 秒；显式 cap 或耗尽 runtime 时会输出 warning，并把剩余 backlog 留给下次运行。调查时可故意使用 `cleanup-alert-data --limit N`，正常清理使用 `--limit 0`。

## Pending outbox 索引与并发

`alertoutboxpending` 是每分钟 publisher 使用的热分区，现在也是权威 enqueue journal，而不再只是 pointer-only index。每个确定性 row 都保存完整、不可变的事件 payload；这一次 Azure Table insert 就是持久 enqueue commit point。Scanner 随后再写入分片的 `alertoutbox` archive row；即使 archive 写入超时，事件仍不会隐藏或丢失，因为 publisher 会直接读取 journal，并在确认 journal 前修复 archive。该协议没有假装 Azure Table 能对两个 partition 提供跨分区 transaction。

Publisher 通过受 ETag 保护的两分钟 lease 领取每个 journal row。重叠执行无法同时持有同一个有效 claim；进程崩溃后完整事件仍保留，lease 到期即可恢复。发布失败时只释放自己仍持有的 lease，不会清除新 owner 的 lease。

Publisher 绝不会仅因 archive 暂时不存在而删除完整 journal row。Service Bus 接受事件后，它先创建 archive 或通过条件更新把 archive 标记成 `published`，之后才幂等删除 journal。任何一个边界发生崩溃，都会使用相同、确定性的 Service Bus `MessageId` 安全重试。滚动部署期间，若旧协议的 pointer-only row 暂时没有 archive，reader 会保留它，避免旧 writer 随后提交的 archive 被永久遗漏；已有 pending archive 的旧 row 会由 legacy migration 转换为完整 journal。该迁移只有在耗尽完整 Azure iterator（包括所有 continuation pages）后才写入带版本的 `_meta/journal-v2` marker，回归测试覆盖 1,205 条旧 rows。日常发现工作只查询单一 `pending` partition，publisher 热路径不会扫描分片 archive table。

Publisher executions 即使重叠也安全：确定性 event ID 与 Service Bus `MessageId`、可恢复 ETag lease、七天 duplicate detection、受 ETag 保护的 archive transition 和幂等 journal acknowledgement 共同工作。系统语义仍为 at-least-once；这些机制让重复可观察且无害，但不宣称数学上的 exactly once。

## 运行身份

- `${prefix}-identity`：仅供 Web；读取 `airco-tracker` Blob，写 `users`、`authcodes`、`authsessions`、`alertrecipients`，读取 i18n，拥有 custom ACS sender role，并且只在 `unsubscribe-signing-key`、`withdrawal-signing-key` 和 `auth-code-hmac-pepper` 三个 secret scope 上拥有 `Key Vault Secrets User`。
- `${prefix}-scanner`：仅供 scanner；写库存 Blob、写 `alertoutbox`/`alertoutboxpending`、拉取 ACR，并且只读取 `awin-publisher-api-token`、`aliexpress-app-key`、`aliexpress-app-secret` 三个 secret。
- `${prefix}-retention`：仅供后端 retention；写 outbox/pending/delivery/index/suppression tables，读取 `alertrecipients`，并拉取 ACR。
- `${prefix}-web-retention`：仅供 Web 认证数据 retention；写 `users`/`authcodes`/`authsessions` 并拉取 ACR，不拥有 alert pipeline、ACS 或 Key Vault 权限。
- `${prefix}-alert-email`：仅供邮件 worker；保留原有 queue/table/ACS 权限，并且只读取 `unsubscribe-signing-key`。

迁移完成后，没有任何 runtime identity 能在整个 vault 范围列举/读取 secret。Secret value 仍通过 out-of-band 方式配置。裸 Bicep 调用默认不会在六个 secret 尚未创建时启用 `manageSecretScopedKeyVaultRbac`；`deploy-azure.sh` 只通过 ARM control plane 检查 secret metadata，并仅在集合完整时自动启用精确 role assignments，整个过程不会读取 secret value。

Foundation 输出 `webIdentity*`、`scannerIdentity*`、`retentionIdentity*` 和 `webRetentionIdentity*`；旧 `identityName` 继续作为 web identity 的 alias，避免破坏前端部署契约。

## 安全迁移与回滚

Foundation/RBAC 必须由本地 Owner-equivalent principal 部署；GitHub 被刻意限制为不能创建 role assignment。

1. 先创建上述六个 Key Vault secrets，再运行 `AZURE_FOUNDATION_ONLY=true ./scripts/deploy-azure.sh`。对已有且完整的 vault，它会创建精确 secret-scoped assignments 以及全部替代身份/RBAC，但不移动任何 workload。如需关闭自动判断并在缺少任一 secret 时直接失败，同时设置 `AZURE_MANAGE_SECRET_SCOPED_KEY_VAULT_RBAC=true`。
2. 先部署 Web 仓库，让 cleanup Job 绑定 `${prefix}-web-retention`；再运行 `./scripts/deploy-application.sh`，让 scanner 和后端 retention 绑定各自专属身份。Application rollback 会同时恢复部署前记录的镜像与 scanner/retention identity names。
3. 验证 scanner、后端 retention、Web 认证数据 retention、publisher、Web 登录/Profile、inventory API，以及一封经授权的定向测试邮件。确认 Web app、scanner job、后端 retention job、Web cleanup job 和 email worker 都使用各自精确身份。
4. 运行 `AZURE_RESOURCE_GROUP=... AZURE_PREFIX=... ./scripts/migrate-runtime-identities.sh`；它不会修改任何内容，只列出明确的旧 grant。
5. 所有验证通过后，才运行同一命令并加 `--apply`。脚本会先验证每个替代 workload binding、每个精确 secret-level grant，以及 Web/email/scanner 必须保留的 grants；随后只删除明确枚举的 legacy grants（包括三个 vault-wide `Key Vault Secrets User` 以及后端 retention 对认证表的旧 grant），最后再次验证保留项和删除项。
6. Dry-run 或 `--apply` 均可重复执行：不存在的 grant 是 no-op，并发 cleanup 也可容忍。

清理前的回滚方式是恢复旧 job identity 及旧 grants。清理后如发现缺权，只补回失败的精确 role 并重新部署；不得授予 subscription/resource-group Contributor。Bicep incremental deployment 不会自动删除旧 role assignments，因此显式 apply 是唯一 destructive boundary。

## 应用 Canary 与回滚

`deploy-application.sh` 会先构建 immutable candidate。如果已存在旧版本，它会生成权限为 `0600`、经过 schema 字段过滤的一次性 execution template：沿用已部署的 command、args、environment 与 CPU/memory，只替换 image；遇到多容器或 volume-backed 等歧义模板会 fail closed。`job start --yaml` 沿用已部署 identity 和依赖，但不会改动任何生产 job definition。Canary 成功后才进行 Bicep 更新，随后对新镜像依次执行 reconciler → scanner → publisher 验证。如果部署或任何部署后检查失败，`EXIT` guard 会自动用已记录的 previous image 重部署所有 application workloads，并再次验证 reconciler。

自动回滚恢复的是可执行代码，不是任意旧版基础设施 schema：它会有意使用当前已评审的 Bicep 参数重新应用 previous immutable image。如果某次发布主动引入了不兼容的 resource configuration，必须保留上一 Git commit，并使用上一版本 template 执行完整配置回滚。Web 仓库独立采用多 revision 流程：旧健康 revision 在候选验证期间保持 100% traffic，通过候选 revision FQDN（包括 `/ready` 依赖检查）验证后才切流；任何失败都会把 100% traffic 恢复到上一 revision。

## 验证门禁

Commit 或部署前运行：

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps .
python -m pip check
python -m unittest discover -v
python -m compileall -q airco_tracker tests
bash -n scripts/*.sh
for file in infra/*.bicep; do az bicep build --file "$file" --stdout >/dev/null; done
git diff --check
```

CI 会在 pull request 和 push `main` 时运行，使用带 hash 的 locked dependencies，以 `--no-deps` 安装项目，并审计同一 lock file。`.venv`（包括同名 symlink）会被忽略，绝不能提交。
