# Lens plugin for OpenCode

Sends each OpenCode turn to LiteLLM Lens as one trace: the prompt, the reply, every model call with its tokens, and every tool call with its input and output. It needs OpenCode 2.3.2 or later. OpenCode 1.x keeps using its own telemetry

The plugin uploads what the agent reads and writes, including source code and command output. It is off until you install it and give it both a URL and a key

## Install

```sh
opencode plugin add github:BerriAI/litellm-lens-plugins
```

Then tell it where to send traces. With a LiteLLM gateway, use the gateway's address and your own LiteLLM key. The gateway records which key, user and team sent the trace, and Lens shows the cost the gateway billed for each model call

```sh
export LITELLM_LENS_OPENCODE_URL=https://litellm.example.com
export LITELLM_LENS_OPENCODE_API_KEY=sk-your-litellm-key
```

With Lens on its own, use the Lens address and a Lens tracing key. Model calls then show tokens and no cost

Restart OpenCode's background service so it picks up the variables

```sh
opencode service stop
```

The same settings can live in `opencode.json` instead of the environment

```json
{
  "plugins": [
    {
      "package": "github:BerriAI/litellm-lens-plugins",
      "options": { "url": "https://litellm.example.com", "apiKey": "sk-your-litellm-key" }
    }
  ]
}
```

## Settings

| Option | Environment variable | Meaning |
| --- | --- | --- |
| `url` | `LITELLM_LENS_OPENCODE_URL` | Gateway or Lens address. `/v1/traces` is added, and a trailing `/v1` (the form a model base URL has) is accepted |
| `apiKey` | `LITELLM_LENS_OPENCODE_API_KEY` | LiteLLM key, or a Lens tracing key |
| `maxContentLength` | `LITELLM_LENS_OPENCODE_MAX_CONTENT_LENGTH` | Longest prompt, reply or tool result kept, in characters. Default 200000 |
| `debug` | `LITELLM_LENS_OPENCODE_DEBUG` | Also print event stream errors |

Options win over environment variables

## What a trace holds

One trace per turn, grouped into a session by OpenCode's session id. Lens shows a session's turns together as one trace

- `invoke_agent opencode`: the turn, with the prompt and the final reply
- `chat <model>`: one per model call, with the system prompt, the messages added since the previous reply, the reply, tokens and the provider's response id. Earlier messages are on the earlier calls, so a session reads top to bottom without repeats. The session title call is included with its prompt and the title, so the turn's cost adds up to what the gateway billed
- `execute_tool <name>`: one per tool call, with its input and result

A subagent's turns sit under the `execute_tool subagent` call that started it, in the parent's trace and session, and carry the subagent's own session id as `opencode.subagent.session.id`

Cost is never taken from OpenCode's own estimate. Lens matches each model call to the gateway's spend record by the provider's response id, which the plugin reads from the start of the response

A trace is sent when the turn ends. A failed delivery is retried twice and then dropped, so a Lens outage never blocks OpenCode. Each dropped trace adds a line with the address and the error to `~/.local/state/litellm-lens/opencode.log` (under `$XDG_STATE_HOME` when set) and to the server's stderr, which `opencode run --print-logs` shows. If no trace arrives, read that file first

## Develop

```sh
cd opencode
npm test
```

There are no dependencies and no build step. To try a local checkout, put its path in `plugins` in `opencode.json`
