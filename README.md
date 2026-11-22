# LiteLLM Lens plugins

Lens plugins for coding agents. Each plugin sends an agent's chats to [LiteLLM Lens](https://docs.litellm.ai/docs/proxy/lens) through your LiteLLM gateway, so one chat is one trace with its prompts, replies, tool calls, model calls and gateway cost.

| Agent | Folder | Status |
| --- | --- | --- |
| Codex | [`codex/`](codex/README.md) | Public preview |
| Claude Code | [`claude-code/`](claude-code/README.md) | Public preview |
| OpenCode | [`opencode/`](opencode/README.md) | Public preview, OpenCode 2.3.2 or later |

Each agent has its own folder with its own README, tests and version. The repository root holds only the marketplace manifests the agents read (`.agents/plugins/marketplace.json` for Codex, `.claude-plugin/marketplace.json` for Claude Code), the root `package.json` OpenCode installs from, the license and this page.

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

## Claude Code

```bash
claude plugin marketplace add BerriAI/litellm-lens-plugins
claude plugin install litellm-lens@berriai-lens
```

Claude Code asks for your LiteLLM gateway URL and a LiteLLM key on install. The plugin stays off while Claude Code's own trace export is on, so a chat is never recorded twice. See [`claude-code/README.md`](claude-code/README.md) for what is recorded and the limits.

## OpenCode

```bash
opencode plugin add github:BerriAI/litellm-lens-plugins
export LITELLM_LENS_OPENCODE_URL=https://litellm.example.com
export LITELLM_LENS_OPENCODE_API_KEY=sk-your-litellm-key
opencode service stop
```

The plugin stays off until both variables are set, because it uploads what the agent reads and writes. See [`opencode/README.md`](opencode/README.md) for what is recorded and the settings.

## License

MIT. See [LICENSE](LICENSE).
