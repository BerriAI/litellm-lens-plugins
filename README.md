# LiteLLM Lens plugins

Lens plugins for coding agents. Each plugin sends an agent's chats to [LiteLLM Lens](https://docs.litellm.ai/docs/proxy/lens) through your LiteLLM gateway, so one chat is one trace with its prompts, replies, tool calls, model calls and gateway cost.

| Agent | Folder | Status |
| --- | --- | --- |
| Codex | [`codex/`](codex/README.md) | Public preview |
| Claude Code | `claude-code/` | Coming in its own pull request |
| OpenCode | `opencode/` | Coming in its own pull request |

Each agent has its own folder with its own README, tests and version. The repository root holds only the marketplace manifests the agents read (`.agents/plugins/marketplace.json` for Codex), the license and this page.

## Codex

```bash
codex plugin marketplace add BerriAI/litellm-lens-plugins
codex plugin add litellm-lens@berriai-lens
```

Or run the installer, which also connects the plugin to your gateway (macOS and Linux, Python 3.11+):

```bash
curl -fsSL https://raw.githubusercontent.com/BerriAI/litellm-lens-plugins/main/codex/install.sh | bash
```

See [`codex/README.md`](codex/README.md) for what is recorded, setup options and limits.

Moving from `BerriAI/litellm-lens-codex-integration`: the plugin keeps its name, `litellm-lens@berriai-lens`, and its local settings. Remove the old marketplace with `codex plugin marketplace remove berriai-lens`, then run the two commands above and `setup` once so Codex trusts the hooks at their new version.

## License

MIT. See [LICENSE](LICENSE).
