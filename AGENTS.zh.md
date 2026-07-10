# Airco Tracker — 共享代理说明

<p align="center">
  <a href="./AGENTS.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/AGENTS-简体中文-d73a49"></a>
  <a href="./AGENTS.md"><img alt="English" src="https://img.shields.io/badge/AGENTS-English-0969da"></a>
</p>

## 使命

维护一个可靠、低成本的库存追踪器，追踪可配送到荷兰/法国等目标国家的真实便携压缩机空调。只在商品首次可购买或从不可购买变为可购买时通知，并为公开只读 dashboard 发布可信的私有库存快照。

## 先读

1. 阅读 `docs/HANDOFF.md`，了解当前状态和下一项任务。
2. 编辑前阅读相关 adapter、测试和基础设施文件。
3. 面向用户的 setup 细节使用语言专属 README（`README.md`、`README.en.md`）。行为变化时保持两者同步。
4. 如果涉及库存 schema 或语义，编辑前检查 `~/airco-tracking-web/server/inventory.ts`、`src/types.ts`、fixture/tests 和前端 handoff。
5. 所有 Markdown 文档都必须维护中文和英语版本。修改任意文档时，在同一变更中更新两个语言版本。

## 不可协商的规则

- 永远不要提交或打印 API keys、client secrets、passwords、access tokens、SMTP credentials 或 Key Vault secret values。
- 第三方凭据放在 Azure Key Vault，并通过 Managed Identity 读取。
- 优先使用官方 API。否则使用公开 server-rendered 页面或 robots-advertised sitemap。尊重 robots.txt 和条款；不要绕过 CAPTCHA、403 防护、登录墙或反爬控制。
- 追踪真实压缩机空调。排除 air coolers、蒸发式冷风机、风扇、软管、窗户套件、遥控器、滤芯和其它配件。
- `available=True` 表示当前可配送到目标地址。仅门店库存、仅自取、过期 deal、预售和多周交期不得触发提醒。
- 单个零售商失败不得阻止其它零售商检查。不要把失败检查当成缺货 transition。
- 测试和 dry-run 不得发送邮件或更新生产状态。
- Alert state 和 live inventory 必须分离。Alert filters 不得把范围内的可购买商品从 `inventory.json` 中移除。
- `inventory.json` schema version `1` 正在被生产 `airco-tracking-web` 消费。没有跨仓库协调和测试，不要静默修改字段、含义或 failure/staleness 行为。
- `airco-tracker` Blob container 必须保持私有。不要把 Storage credentials 或 SAS URL 暴露给浏览器代码；前端同源 Node API 使用 Managed Identity 读取。
- 当 product-catalog 和 affiliate-offer scope 已足够时，不要读取订单、买家、支付或其它个人数据。
- 保留 dirty worktree 中与任务无关的用户改动。

## 架构

- Python package：`airco_tracker/`
- 国家无关 parsing helpers：`airco_tracker/adapters/base.py`（`Adapter` ABC、price/BTU/presale parsing）、`schema.py`（JSON-LD）、`sitemap.py`
- 国家 adapter registry：`airco_tracker/adapters/registry.py` — `load_adapter_specs(countries)` 将每个 adapter 绑定到明确 country/site_id 并 fail-fast 验证重复项；`load_adapter_classes(countries)` 仍可供 class-only caller 使用
- 零售商集成：`airco_tracker/adapters/nl/`、`airco_tracker/adapters/fr/`；新增国家时添加 `adapters/<country>/`
- CLI/orchestration：`airco_tracker/cli.py`
- 状态 transition：`airco_tracker/state.py`
- 库存 snapshot builder：`airco_tracker/inventory.py`
- State/inventory persistence：`airco_tracker/state_store.py`、`airco_tracker/inventory_store.py`
- Azure infrastructure：`infra/`
- 部署脚本：`scripts/`
- 测试：`tests/`
- 生产：Azure Container Apps scheduled job、private Blob Storage alert state/inventory、Communication Services Email、Key Vault 和 Managed Identity。
- CI/CD：push 到 `main` 运行测试、构建 commit SHA tagged immutable image、部署并启动一次 verification execution。纯 Markdown/docs 改动被部署 workflow 忽略。
- Consumer：`~/airco-tracking-web` 提供公开 dashboard，并通过共享 runtime identity 的 `/api/inventory` 读取私有 snapshot。

## 标准验证

从仓库根目录运行：

```bash
.venv/bin/python -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/airco-pycache .venv/bin/python -m compileall -q airco_tracker tests
git diff --check
.venv/bin/python -m airco_tracker check --dry-run
```

Live dry-run 会执行网络读取，但不得发送邮件或修改状态。如果本地安装的 `airco-tracker` entry point 过期，用 `.venv/bin/pip install --no-deps --force-reinstall .` 重装，或使用 `python -m airco_tracker`。

库存 contract 变化还必须在 `~/airco-tracking-web` 中运行：

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm typecheck
pnpm build
PORT=4174 INVENTORY_FILE=public/inventory.sample.json pnpm start
node scripts/verify-deployment.mjs http://127.0.0.1:4174
```

在同一协调变更中更新前端 server validator、browser types、fixture、tests 和 handoff。仅后端测试通过不足以验证 schema 变化。

## 变更流程

1. 检查 `git status` 和最近历史。
2. 做最小连贯变更，并添加聚焦 parser/state tests。
3. 运行 unit tests、compile checks 和 `git diff --check`。
4. 零售商变更需要执行 live `--dry-run` 并检查 retailer counts/errors。
5. 库存 contract 变化需要验证两个仓库并保持 schema versioning 明确。
6. 支持站点、配置或部署行为变化时，更新中英两个 README。
7. 当前状态、已部署 commit、外部审核状态、前端 contract、下一项任务或 blocker 变化时，更新 `docs/HANDOFF.md` 及其语言版本。
8. 只有用户请求授权时，才 commit、push、deploy 或启动生产 job。

## 交接质量

保持 `docs/HANDOFF.md` 事实化且紧凑。记录日期、已部署 commit、已完成工作、当前 blocker、下一步和验证证据。不要写入 secrets 或不必要的个人数据。
