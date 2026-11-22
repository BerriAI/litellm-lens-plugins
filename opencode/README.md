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
| `url` | `LITELLM_LENS_OPENCODE_URL` | Gateway or Lens address. `/v1/traces` is added when missing |
| `apiKey` | `LITELLM_LENS_OPENCODE_API_KEY` | LiteLLM key, or a Lens tracing key |
| `maxContentLength` | `LITELLM_LENS_OPENCODE_MAX_CONTENT_LENGTH` | Longest prompt, reply or tool result kept, in characters. Default 200000 |
| `debug` | `LITELLM_LENS_OPENCODE_DEBUG` | Print delivery failures to the service log |

Options win over environment variables

## What a trace holds

One trace per turn, grouped into a session by OpenCode's session id

- `invoke_agent opencode`: the turn, with the prompt and the final reply
- `chat <model>`: one per model call, with the system prompt, the messages sent, the reply, tokens and the provider's response id. The session title call is included without content so the turn's cost adds up to what the gateway billed
- `execute_tool <name>`: one per tool call, with its input and result

Cost is never taken from OpenCode's own estimate. Lens matches each model call to the gateway's spend record by the provider's response id, which the plugin reads from the start of the response

A trace is sent when the turn ends. A failed delivery is retried twice and then dropped, so a Lens outage never blocks OpenCode

## Develop

```sh
cd opencode
npm test
```

There are no dependencies and no build step. To try a local checkout, put its path in `plugins` in `opencode.json`
