"""Read visible session items within one hook-authorized turn."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
from pathlib import Path
from typing import Iterator

from .content import encode

MAX_RECORD_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Entry:
    identity: str
    start: float
    end: float
    role: str = "assistant"
    text: str = ""
    model: str | None = None
    tool: str | None = None
    arguments: object = None
    result: object = None
    error: str | None = None


@dataclass(frozen=True)
class ModelCall:
    response_id: str
    end: float
    usage: dict
    model: str | None = None


@dataclass(frozen=True)
class Recording:
    entries: tuple[Entry, ...] = ()
    complete: bool = False
    warning: str | None = None
    ended: float | None = None
    error: str | None = None
    calls: tuple[ModelCall, ...] = ()


def records(path: str, offset: int = 0) -> Iterator[dict]:
    with Path(path).open("rb") as stream:
        beginning = max(0, offset - MAX_RECORD_BYTES - 256 * 1024)
        stream.seek(beginning)
        if beginning:
            stream.readline(MAX_RECORD_BYTES)
        while raw := stream.readline(MAX_RECORD_BYTES + 1):
            after_offset = stream.tell() > offset
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError("A transcript record exceeds the supported size.")
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(record, dict):
                yield {**record, "_lens_after_offset": after_offset}


def timestamp(value: object, fallback: float = 0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def visible_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type", "")).lower()
        if kind in {"text", "input_text", "output_text"} and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind in {"image", "input_image", "image_url", "localimage", "local_image", "file", "document"}:
            parts.append("[Attachment omitted by Lens]")
    return "\n".join(parts)


def codex_item(item: dict, start: float, end: float, model: str | None) -> Entry | None:
    kind, identity = item.get("type"), str(item.get("id", ""))
    common = dict(identity=identity, start=start, end=end, model=model)
    if kind in {"UserMessage", "AgentMessage"}:
        return Entry(**common, role="user" if kind == "UserMessage" else "assistant",
                     text=visible_text(item.get("content")))
    if kind == "Reasoning":
        return None
    if kind == "SubAgentActivity":
        return Entry(**common, role="system", text=f"Agent {item.get('agent_path', 'subagent')} {item.get('kind', 'updated')}")
    if kind == "CommandExecution":
        command = item.get("command", [])
        command = command[-1] if isinstance(command, list) and len(command) >= 3 and command[-2] in {"-lc", "-c"} else command
        code = item.get("exit_code")
        error = f"Command exited with code {code}." if isinstance(code, int) and code != 0 else None
        if not error and item.get("status") in {"failed", "declined"}:
            error = "Command " + item["status"] + "."
        return Entry(**common, tool="Bash", arguments={"command": command},
                     result={"output": item.get("aggregated_output", ""), "exit_code": code}, error=error)
    if kind == "FileChange":
        return Entry(**common, tool="apply_patch", arguments={"changes": item.get("changes", [])},
                     result=item.get("status", "unknown"),
                     error="File change failed." if item.get("status") in {"failed", "declined"} else None)
    if kind == "McpToolCall":
        result, error = item.get("result"), item.get("error")
        failed = item.get("status") == "failed" or isinstance(result, dict) and result.get("isError") is True
        return Entry(**common, tool=str(item.get("tool", "MCP tool")), arguments=item.get("arguments"),
                     result=result, error=encode(error) if error else ("Tool returned an error." if failed else None))
    if kind == "CollabAgentToolCall":
        return Entry(**common, tool=str(item.get("tool", "Agent")),
                     arguments={k: item[k] for k in ("prompt", "receiver_thread_ids") if k in item},
                     result=item.get("agents_states", {}),
                     error="Agent operation failed." if item.get("status") == "failed" else None)
    if kind == "WebSearch":
        return Entry(**common, tool="Web search", arguments=item.get("action", {"query": item.get("query")}),
                     result=item.get("status", "completed"))
    if kind == "ContextCompaction":
        return Entry(**common, role="system", text="Context compacted")
    if kind in {"ImageView", "ImageGeneration"}:
        return Entry(**common, text="[Image omitted by Lens]")
    return Entry(**common, role="system", text=f"[Session item not supported by Lens: {kind}]")


USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                "reasoning_output_tokens")


def model_call(payload: dict, at: float, model: str | None) -> ModelCall | None:
    """One record per model response, written by Codex with the response ID the gateway also logs."""
    identity, usage = payload.get("response_id"), payload.get("usage")
    if not isinstance(identity, str) or not identity or not isinstance(usage, dict):
        return None
    counts = {key: usage.get(key, 0) for key in USAGE_FIELDS}
    if not all(type(value) is int and value >= 0 for value in counts.values()):
        return None
    return ModelCall(identity, at, counts, model)


def timed(entries: dict[str, Entry], ran: dict[str, float], windows: list[tuple[float, float]]) -> dict[str, Entry]:
    """Codex stamps a tool item with one instant, so time it from the model's call record to its output record."""
    result, previous = {}, {}
    for identity, at in sorted(ran.items(), key=lambda item: item[1]):
        window = max((w for w in windows if w[0] <= at <= w[1]), default=None)
        if window:
            start = previous.get(window, window[0])
            later = any(window[0] <= other <= window[1] and other > at for other in ran.values())
            previous[window] = end = at if later else window[1]
            result[identity] = replace(entries[identity], start=start, end=max(start, end))
    return result


def codex_recording(path: str, offset: int, turn_id: str) -> Recording:
    current, model, complete = None, None, False
    ended, error = None, None
    entries: dict[str, Entry] = {}
    fallback: dict[str, Entry] = {}
    calls: dict[str, ModelCall] = {}
    opened: dict[str, float] = {}
    windows: list[tuple[float, float]] = []
    ran: dict[str, float] = {}
    for index, record in enumerate(records(path, offset)):
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        kind, record_type = payload.get("type"), record.get("type")
        if record_type == "turn_context":
            current, model = payload.get("turn_id"), payload.get("model")
        if record_type == "event_msg" and kind == "task_started":
            current = payload.get("turn_id")
        if current != turn_id:
            continue
        at = timestamp(record.get("timestamp"))
        if record_type == "token_usage_record" and payload.get("turn_id") == turn_id:
            call = model_call(payload, at, model)
            if call:
                calls[call.response_id] = call
        if record_type == "event_msg" and kind == "item_completed" and payload.get("turn_id") == turn_id:
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            start = timestamp(payload.get("started_at_ms"), at * 1000) / 1000
            end = timestamp(payload.get("completed_at_ms"), at * 1000) / 1000
            entry = codex_item(item, start, end, model)
            if entry:
                entries[entry.identity or str(index)] = entry
                if entry.tool:
                    ran[entry.identity or str(index)] = at
        if record_type == "response_item" and isinstance(payload.get("call_id"), str):
            if str(kind).endswith("_call"):
                opened[payload["call_id"]] = at
            elif str(kind).endswith("_call_output") and payload["call_id"] in opened:
                windows.append((opened.pop(payload["call_id"]), at))
        if record_type == "response_item" and kind == "message" and payload.get("role") == "assistant" and payload.get("channel") != "analysis":
            identity = str(payload.get("id") or index)
            fallback[identity] = Entry(identity, at, at, text=visible_text(payload.get("content")), model=model)
        if record_type == "event_msg" and kind == "error":
            error = str(payload.get("message") or "Turn failed.")
        if record_type == "event_msg" and kind in {"task_complete", "turn_aborted"} and payload.get("turn_id") == turn_id:
            complete, ended = True, at
            if kind == "turn_aborted":
                error = "Turn interrupted before a final reply."
            current = None
    for identity, entry in fallback.items():
        entries.setdefault(identity, entry)
    entries.update(timed(entries, ran, windows))
    return Recording(tuple(sorted(entries.values(), key=lambda entry: entry.start)), complete, ended=ended,
                     error=error, calls=tuple(sorted(calls.values(), key=lambda call: call.end)))


def read_recording(path: str | None, offset: int, turn_id: str) -> Recording:
    if not path:
        return Recording(warning="Transcript unavailable; only lifecycle hook content was captured.")
    try:
        result = codex_recording(path, offset, turn_id)
        return result if result.entries else Recording(calls=result.calls, complete=result.complete, ended=result.ended, error=result.error, warning="No supported transcript items were found.")
    except (OSError, ValueError, TypeError, RecursionError):
        return Recording(warning="Transcript could not be read; only lifecycle hook content was captured.")


def codex_parent(path: Path, turn_id: str, offset: int = 0) -> tuple[str | None, str | None, str | None]:
    first = next(records(str(path)), {})
    payload = first.get("payload", {})
    source = payload.get("source", {}) if isinstance(payload, dict) else {}
    subagent = source.get("subagent", {}) if isinstance(source, dict) else {}
    spawn = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
    spawn = spawn if isinstance(spawn, dict) else {}
    parent, name = spawn.get("parent_thread_id"), spawn.get("agent_path")
    for record in records(str(path), offset):
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "event_msg" and payload.get("type") == "task_started" and payload.get("turn_id") == turn_id:
            return parent, payload.get("root_turn_id"), name
    return parent, None, name
