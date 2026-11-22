"""Hook entry point. The hook itself only starts a detached exporter, so Claude Code never waits on the network."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .trace import envelope, stable_id, turn_spans
from .transcript import Turn, read_lines, turns

BATCH_LIMIT = 512 * 1024
SETTLE_SECONDS = float(os.environ.get("LENS_SETTLE_SECONDS", "8"))


def settings() -> dict | None:
    env = os.environ
    url = env.get("CLAUDE_PLUGIN_OPTION_LENS_URL") or env.get("LENS_URL")
    key = env.get("CLAUDE_PLUGIN_OPTION_LENS_KEY") or env.get("LENS_TRACING_KEY")
    if not url or not key or native_export_is_on():
        return None
    return {"url": url.rstrip("/").removesuffix("/v1/traces"), "key": key}


def native_export_is_on() -> bool:
    """Claude Code hides OTEL_* from hooks, so its own trace export is detected by the two flags that turn it on."""
    return all(os.environ.get(name, "").lower() in ("1", "true")
               for name in ("CLAUDE_CODE_ENABLE_TELEMETRY", "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"))


def state_dir() -> Path:
    path = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".claude" / "lens")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def batches(spans: list[dict]) -> list[list[dict]]:
    result: list[list[dict]] = [[]]
    size = 0
    for item in spans:
        weight = len(json.dumps(item, ensure_ascii=False).encode())
        if result[-1] and size + weight > BATCH_LIMIT:
            result.append([])
            size = 0
        result[-1].append(item)
        size += weight
    return [batch for batch in result if batch]


def send(config: dict, spans: list[dict]) -> str | None:
    for batch in batches(spans):
        body = json.dumps(envelope(batch), ensure_ascii=False).replace(config["key"], "[redacted]").encode()
        request = urllib.request.Request(config["url"] + "/v1/traces", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + config["key"],
            "User-Agent": "litellm-lens-claude-code/" + __version__})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
        except urllib.error.HTTPError as failure:
            return f"Lens rejected the trace: HTTP {failure.code}"
        except (urllib.error.URLError, OSError) as failure:
            return f"Could not reach Lens: {failure}"
    return None


def settled_turns(path: str, final: bool) -> list[Turn]:
    deadline = time.monotonic() + SETTLE_SECONDS
    while True:
        found = turns(read_lines(path))
        if not found or found[-1].settled or time.monotonic() > deadline:
            return found if final or not found or found[-1].settled else found[:-1]
        time.sleep(0.5)


def export(event: dict, config: dict) -> str | None:
    session = event.get("session_id")
    subagent = event.get("hook_event_name") == "SubagentStop"
    path = event.get("agent_transcript_path") if subagent else event.get("transcript_path")
    if not session or not path:
        return None
    path = os.path.expanduser(path)
    name = stable_id(session, path, size=12) + ".json"
    with open(state_dir() / name, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        done = json.loads(handle.read() or "{}").get("line", -1)
        found = settled_turns(path, final=True)
        fresh = [turn for turn in found if turn.last_line > done]
        if not fresh:
            return None
        parent = agent = None
        if subagent:
            main = turns(read_lines(os.path.expanduser(event.get("transcript_path") or "")))
            parent = stable_id("claude-code-turn", session, main[-1].identity, size=8) if main else None
            agent = event.get("agent_type") or "subagent"
        spans = [item for turn in fresh for item in turn_spans(
            session, turn, agent or "claude-code", parent, event.get("agent_id") if subagent else None)]
        failure = send(config, spans)
        if failure:
            return failure
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"line": fresh[-1].last_line}))
    return None


def main(arguments: list[str]) -> int:
    config = settings()
    if arguments[:1] == ["export"]:
        event = json.loads(sys.stdin.read() or "{}")
        failure = export(event, config) if config else None
        log = state_dir() / "last_error.txt"
        if failure:
            log.write_text(failure + "\n")
        elif log.exists():
            log.unlink()
        return 0
    payload = sys.stdin.read()
    if not config:
        return 0
    child = subprocess.Popen([sys.executable, "-m", "lens_claude_code", "export"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    child.stdin.write(payload.encode())
    child.stdin.close()
    return 0
