"""Reads a Claude Code session transcript (JSONL) into turns, model calls and tool calls."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_READ = 256 * 1024 * 1024


@dataclass
class Tool:
    identity: str
    name: str
    arguments: object
    start: float
    end: float | None = None
    result: object = None
    error: bool = False


@dataclass
class ModelCall:
    message_id: str
    request_id: str | None
    model: str
    start: float
    end: float
    usage: dict
    inputs: list[dict] = field(default_factory=list)
    content: list[dict] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass
class Turn:
    identity: str
    prompt: object
    start: float
    end: float
    calls: list[ModelCall] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    error: str | None = None
    last_line: int = 0

    @property
    def settled(self) -> bool:
        finished = bool(self.calls) and self.calls[-1].stop_reason not in (None, "tool_use")
        return (finished or bool(self.error)) and all(tool.end is not None for tool in self.tools)


def seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_lines(path: str) -> list[dict]:
    try:
        raw = Path(path).read_bytes()[:MAX_READ]
    except OSError:
        return []
    records = []
    for line in raw.split(b"\n"):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            record = None
        records.append(record if isinstance(record, dict) else {})
    if records and not raw.endswith(b"\n"):
        records.pop()
    return records


def is_prompt(record: dict) -> bool:
    if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and bool(content) and not any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def turns(records: list[dict]) -> list[Turn]:
    result: list[Turn] = []
    calls: dict[str, ModelCall] = {}
    tools: dict[str, Tool] = {}
    pending: list[dict] = []
    requested: float | None = None
    previous: float | None = None
    for index, record in enumerate(records):
        stamp = seconds(record.get("timestamp"))
        kind = record.get("type")
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = message.get("content")
        if is_prompt(record):
            start = stamp or previous or 0.0
            result.append(Turn(record.get("uuid") or str(index), content, start, start, last_line=index))
            pending = [{"role": "user", "content": content}]
            requested = None
        elif not result:
            pass
        elif kind == "api-request":
            requested = stamp
        elif kind == "assistant" and message.get("model") == "<synthetic>":
            result[-1].error = text_of(content) or "The request failed."
        elif kind == "assistant" and message.get("id"):
            turn = result[-1]
            identity = message["id"]
            end = stamp or previous or turn.start
            call = calls.get(identity)
            if call is None:
                call = calls[identity] = ModelCall(identity, record.get("requestId"), message.get("model") or "",
                                                   requested or previous or turn.start, end, {}, pending)
                turn.calls.append(call)
                pending, requested = [], None
            call.end = max(call.end, end)
            call.usage = message.get("usage") or call.usage
            call.stop_reason = message.get("stop_reason") or call.stop_reason
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                call.content.append(block)
                if block.get("type") == "tool_use" and block.get("id"):
                    tool = tools[block["id"]] = Tool(block["id"], block.get("name") or "Tool", block.get("input"), end)
                    turn.tools.append(tool)
        elif kind == "user" and isinstance(content, list):
            for block in content:
                tool = tools.get(block.get("tool_use_id")) if isinstance(block, dict) else None
                if tool is None:
                    continue
                tool.end, tool.result, tool.error = stamp or tool.start, block.get("content"), bool(block.get("is_error"))
            pending.append({"role": "user", "content": content})
        if result and stamp:
            result[-1].end = max(result[-1].end, stamp)
        if result and kind in ("user", "assistant"):
            result[-1].last_line = index
        previous = stamp or previous
    return result


def text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block.get("text", "") for block in content
                     if isinstance(block, dict) and block.get("type") == "text")
