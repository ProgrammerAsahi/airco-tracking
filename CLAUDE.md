@AGENTS.md
@docs/HANDOFF.md

# Claude Code notes

<p align="center">
  <a href="./CLAUDE.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/CLAUDE-简体中文-d73a49"></a>
  <a href="./CLAUDE.md"><img alt="English" src="https://img.shields.io/badge/CLAUDE-English-0969da"></a>
  <a href="./CLAUDE.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/CLAUDE-Nederlands-f58220"></a>
</p>

- Treat `AGENTS.md` as the stable project contract and `docs/HANDOFF.md` as the current operational handoff.
- Start work from the repository root (`~/airco-tracking`) and verify the current branch, working tree, and latest commit before changing files.
- Handoff facts can become stale. Re-check live GitHub/Azure/external-review state before acting on time-sensitive claims.
- Never ask the user to paste an API secret into chat. Use a hidden terminal prompt and Azure Key Vault for credentials.
- `inventory.json` is now a production contract consumed by `~/airco-tracking-web`. Before changing its schema or semantics, inspect the frontend validator/types and coordinate both repositories.
- Keep the inventory Blob private. The public dashboard must continue to read it through its same-origin Managed Identity API, never through a browser-side Storage Key or SAS token.
- Both repositories are public. Keep Git author configuration repository-local and use the existing GitHub noreply identity instead of a machine-derived author.
- Any Markdown documentation change must update the Chinese, English, and Dutch variants together.
- If the requested work reaches an external submission, purchase, permission change, credential creation, or production mutation not already authorized by the user, pause immediately before that action.
- After completing a meaningful milestone or discovering a blocker, update `docs/HANDOFF.md` in the same change.
