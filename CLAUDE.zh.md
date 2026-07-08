@AGENTS.md
@docs/HANDOFF.md

# Claude Code 备注

<p align="center">
  <a href="./CLAUDE.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/CLAUDE-简体中文-d73a49"></a>
  <a href="./CLAUDE.md"><img alt="English" src="https://img.shields.io/badge/CLAUDE-English-0969da"></a>
  <a href="./CLAUDE.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/CLAUDE-Nederlands-f58220"></a>
</p>

- 将 `AGENTS.md` 视为稳定的项目契约，将 `docs/HANDOFF.md` 视为当前运行交接。
- 从仓库根目录 (`~/airco-tracking`) 开始工作，并在修改文件前确认当前分支、工作区和最新提交。
- 交接事实可能过期。处理时效性事项前，请重新检查 GitHub、Azure 和外部审核状态。
- 永远不要要求用户把 API secret 粘贴到聊天里。需要凭据时使用隐藏终端 prompt 和 Azure Key Vault。
- `inventory.json` 现在是被 `~/airco-tracking-web` 消费的生产契约。修改 schema 或语义前，必须检查前端 validator/types 并协调两个仓库。
- 保持 inventory Blob 私有。公共 dashboard 必须继续通过同源 Managed Identity API 读取，而不是通过浏览器侧 Storage Key 或 SAS token。
- 两个仓库都是公开仓库。Git author 配置应保持仓库本地，并使用现有 GitHub noreply 身份，不要使用机器推断作者。
- 任何 Markdown 文档变更都必须同时更新中文、英语和荷兰语版本。
- 如果用户请求触及外部提交、购买、权限变更、凭据创建或尚未授权的生产变更，在执行前必须暂停并请求明确授权。
- 完成有意义的里程碑或发现 blocker 后，在同一变更中更新 `docs/HANDOFF.md`。
