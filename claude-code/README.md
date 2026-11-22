# LiteLLM Lens for Claude Code

A Claude Code plugin that sends each finished turn to LiteLLM Lens: the prompt, every model call with its tokens, every tool call with its input and output, and the reply. It reads the session transcript that Claude Code already writes, so content is complete and Claude Code's own telemetry export does not need to be on.

## Install

```bash
claude plugin marketplace add BerriAI/litellm-lens-plugins
claude plugin install litellm-lens@berriai-lens
```

Claude Code asks for two values on install: the LiteLLM gateway URL and a LiteLLM key. The gateway records the key, user and team on the trace and forwards it to Lens; a standalone Lens takes its own URL and a Lens tracing key instead. The key is kept in Claude Code's secure storage. The environment variables `LENS_URL` and `LENS_TRACING_KEY` work as well when the plugin options are empty. Python 3.10 or later must be on the path; the plugin uses the standard library only.

## What is sent

One session is one trace. Each prompt is an agent step, each model call under it is its own step carrying the model, the provider message id, the request id and token counts including cache tokens, and each tool call is a step with its arguments and result, including failed tools. Subagent turns nest under the turn that started them. Lens matches each model call to the gateway's spend record by the provider message id, so cost and the LiteLLM key, user and team come from the gateway when Claude Code is routed through it.

Thinking blocks, images and documents are left out. A single field is capped at 128 KiB with the middle removed, and one request at 512 KiB.

## How it works

The `Stop`, `StopFailure`, `SubagentStop` and `SessionEnd` hooks start a detached process and return at once, so Claude Code never waits on the network. That process reads the new part of the transcript, groups it into turns, and posts OTLP/JSON to `/v1/traces`. Its position in the transcript moves forward only after Lens accepts the request, so a failed send is retried on the next hook; the last failure is written to `last_error.txt` in the plugin's data directory.

The plugin sends nothing when Claude Code's own trace export is on (`CLAUDE_CODE_ENABLE_TELEMETRY=1` together with `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`), so a session is never recorded twice. Remove those two settings to record with the plugin.

## Tests

```bash
LENS_SETTLE_SECONDS=0 python3 -m unittest discover -s tests
```
