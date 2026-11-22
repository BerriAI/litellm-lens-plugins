"""Turns a parsed turn into OTLP/JSON spans: one agent span, one span per model call, one per tool."""
from __future__ import annotations

import hashlib
import json

from . import __version__
from .transcript import ModelCall, Tool, Turn, text_of

AGENT = "claude-code"
SCOPE = "berriai.lens.claude-code"
FIELD_LIMIT = 128 * 1024


def stable_id(*parts: str, size: int = 16) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:size * 2]


def trace_id(session: str) -> str:
    return stable_id(AGENT + "-session", session)


def excerpt(text: str) -> str:
    raw = text.encode()
    if len(raw) <= FIELD_LIMIT:
        return text
    half = FIELD_LIMIT // 2
    marker = f"\n[... {len(raw) - 2 * half} bytes omitted by the Lens plugin ...]\n"
    return raw[:half].decode(errors="ignore") + marker + raw[-half:].decode(errors="ignore")


def parts(content: object) -> list[dict]:
    """Anthropic content blocks as OpenTelemetry GenAI message parts."""
    if isinstance(content, str):
        return [{"type": "text", "content": content}]
    result = []
    for block in content if isinstance(content, list) else []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            result.append({"type": "text", "content": block.get("text", "")})
        elif kind == "tool_use":
            result.append({"type": "tool_call", "id": block.get("id"), "name": block.get("name"),
                           "arguments": block.get("input")})
        elif kind == "tool_result":
            result.append({"type": "tool_call_response", "id": block.get("tool_use_id"),
                           "response": plain(block.get("content"))})
        elif kind in ("image", "document"):
            result.append({"type": "text", "content": f"[{kind} omitted]"})
    return result


def plain(content: object) -> object:
    if not isinstance(content, list):
        return content
    texts = [part["content"] for part in parts(content)]
    return "\n".join(texts) if all(isinstance(text, str) for text in texts) else texts


def messages(items: list[dict]) -> str:
    """Messages as GenAI parts; a tool result is a tool message, not something the user typed."""
    result = []
    for item in items:
        start = len(result)
        for part in parts(item["content"]):
            role = "tool" if part["type"] == "tool_call_response" else item["role"]
            if len(result) > start and result[-1]["role"] == role:
                result[-1]["parts"].append(part)
            else:
                result.append({"role": role, "parts": [part]})
    return encode(result)


def encode(value: object) -> str:
    return excerpt(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def attribute(key: str, value: object) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def span(trace: str, identity: str, name: str, start: float, end: float, values: dict,
         parent: str | None = None, error: str | None = None) -> dict:
    result = {"traceId": trace, "spanId": identity, "name": name, "kind": 1,
              "startTimeUnixNano": str(int(start * 1e9)), "endTimeUnixNano": str(int(max(start, end) * 1e9)),
              "attributes": [attribute(key, value) for key, value in values.items() if value is not None],
              "status": {"code": 2, "message": excerpt(error)} if error else {"code": 1}}
    if parent:
        result["parentSpanId"] = parent
    return result


def call_span(trace: str, parent: str, session: str, call: ModelCall, agent: str) -> dict:
    usage = call.usage
    cached, written = usage.get("cache_read_input_tokens") or 0, usage.get("cache_creation_input_tokens") or 0
    values = {
        "gen_ai.operation.name": "chat", "gen_ai.provider.name": "anthropic", "gen_ai.agent.name": agent,
        "gen_ai.conversation.id": session, "gen_ai.request.model": call.model, "gen_ai.response.model": call.model,
        "gen_ai.response.id": call.message_id, "anthropic.request_id": call.request_id,
        "gen_ai.response.finish_reasons": json.dumps([call.stop_reason]) if call.stop_reason else None,
        "gen_ai.usage.input_tokens": (usage.get("input_tokens") or 0) + cached + written,
        "gen_ai.usage.output_tokens": usage.get("output_tokens") or 0,
        "gen_ai.usage.cache_read.input_tokens": cached,
        "gen_ai.usage.cache_creation.input_tokens": written,
        "gen_ai.input.messages": messages(call.inputs),
        "gen_ai.output.messages": messages([{"role": "assistant", "content": call.content}]),
    }
    return span(trace, stable_id(AGENT + "-call", session, call.message_id, size=8),
                f"chat {call.model}", call.start, call.end, values, parent)


def tool_span(trace: str, parent: str, session: str, tool: Tool, agent: str, turn_end: float) -> dict:
    values = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool.name,
              "gen_ai.tool.call.id": tool.identity, "gen_ai.agent.name": agent,
              "gen_ai.tool.call.arguments": encode(tool.arguments),
              "gen_ai.tool.call.result": encode(plain(tool.result)) if tool.result is not None else None}
    error = None
    if tool.end is None:
        error = "Tool did not complete before the turn ended."
    elif tool.error:
        error = encode(plain(tool.result))
    return span(trace, stable_id(AGENT + "-tool", session, tool.identity, size=8), tool.name,
                tool.start, tool.end or turn_end, values, parent, error)


def turn_spans(session: str, turn: Turn, agent: str = AGENT, parent: str | None = None,
               agent_id: str | None = None) -> list[dict]:
    trace = trace_id(session)
    root = stable_id(AGENT + "-turn", session, turn.identity, size=8)
    reply = next((call.content for call in reversed(turn.calls) if text_of(call.content)), [])
    values = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent,
              "gen_ai.agent.id": agent_id or session, "gen_ai.conversation.id": session,
              "claude_code.session.id": session, "lens.capture.source": "session_transcript",
              "gen_ai.request.model": turn.calls[-1].model if turn.calls else None,
              "gen_ai.input.messages": messages([{"role": "user", "content": turn.prompt}]),
              "gen_ai.output.messages": messages([{"role": "assistant", "content": text_of(reply)}])}
    spans = [span(trace, root, f"invoke_agent {agent}", turn.start, turn.end, values, parent, turn.error)]
    spans += [call_span(trace, root, session, call, agent) for call in turn.calls]
    spans += [tool_span(trace, root, session, tool, agent, turn.end) for tool in turn.tools]
    return spans


def envelope(spans: list[dict]) -> dict:
    resource = {"service.name": AGENT, "gen_ai.agent.name": AGENT,
                "telemetry.sdk.name": "litellm-lens-claude-code", "telemetry.sdk.version": __version__}
    return {"resourceSpans": [{"resource": {"attributes": [attribute(k, v) for k, v in resource.items()]},
                               "scopeSpans": [{"scope": {"name": SCOPE, "version": __version__}, "spans": spans}]}]}
