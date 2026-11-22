import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lens_claude_code.trace import trace_id, turn_spans  # noqa: E402
from lens_claude_code.transcript import read_lines, turns  # noqa: E402


def line(kind, stamp, **fields):
    return json.dumps({"type": kind, "timestamp": f"2026-01-01T00:00:{stamp:02d}.000Z", **fields})


def assistant(stamp, identity, content, stop, request="req_1"):
    return line("assistant", stamp, requestId=request, uuid=f"a{stamp}", message={
        "id": identity, "model": "claude-test", "content": content, "stop_reason": stop,
        "usage": {"input_tokens": 2, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5,
                  "output_tokens": 7}})


SESSION = [
    line("user", 0, uuid="u1", message={"role": "user", "content": "list files"}),
    line("api-request", 1),
    assistant(2, "msg_1", [{"type": "thinking", "thinking": "secret"}], None),
    assistant(3, "msg_1", [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}],
              "tool_use"),
    line("user", 4, uuid="u2", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.txt", "is_error": False}]}),
    line("api-request", 5),
    assistant(6, "msg_2", [{"type": "text", "text": "One file."}], "end_turn", "req_2"),
    line("user", 7, uuid="u3", message={"role": "user", "content": "thanks"}),
]


def attributes(span):
    return {item["key"]: next(iter(item["value"].values())) for item in span["attributes"]}


class Transcript(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "session.jsonl"
        self.path.write_text("\n".join(SESSION) + "\n")
        self.turns = turns(read_lines(str(self.path)))

    def tearDown(self):
        self.directory.cleanup()

    def test_each_prompt_starts_a_turn_and_tool_results_do_not(self):
        self.assertEqual([turn.prompt for turn in self.turns], ["list files", "thanks"])
        self.assertTrue(self.turns[0].settled)
        self.assertFalse(self.turns[1].settled)

    def test_streamed_lines_with_one_message_id_are_one_model_call(self):
        calls = self.turns[0].calls
        self.assertEqual([call.message_id for call in calls], ["msg_1", "msg_2"])
        self.assertEqual((calls[0].start, calls[0].end), (calls[0].end - 2, calls[0].end))
        self.assertEqual(calls[1].inputs[0]["content"][0]["tool_use_id"], "toolu_1")

    def test_spans_carry_the_ids_lens_matches_cost_on_and_hide_thinking(self):
        spans = turn_spans("s1", self.turns[0])
        self.assertEqual([span["name"] for span in spans],
                         ["invoke_agent claude-code", "chat claude-test", "chat claude-test", "Bash"])
        self.assertEqual({span["traceId"] for span in spans}, {trace_id("s1")})
        self.assertEqual({span.get("parentSpanId") for span in spans[1:]}, {spans[0]["spanId"]})
        call = attributes(spans[1])
        self.assertEqual(call["gen_ai.response.id"], "msg_1")
        self.assertEqual(call["anthropic.request_id"], "req_1")
        self.assertEqual((call["gen_ai.usage.input_tokens"], call["gen_ai.usage.output_tokens"]), ("17", "7"))
        self.assertNotIn("secret", json.dumps(spans))
        self.assertEqual(json.loads(call["gen_ai.output.messages"]), [{"role": "assistant", "parts": [
            {"type": "tool_call", "id": "toolu_1", "name": "Bash", "arguments": {"command": "ls"}}]}])
        self.assertEqual(json.loads(attributes(spans[2])["gen_ai.input.messages"]), [{"role": "tool", "parts": [
            {"type": "tool_call_response", "id": "toolu_1", "response": "a.txt"}]}])
        self.assertEqual(attributes(spans[3])["gen_ai.tool.call.result"], "a.txt")
        self.assertIn("One file.", attributes(spans[0])["gen_ai.output.messages"])

    def test_a_partial_last_line_is_ignored(self):
        self.path.write_text("\n".join(SESSION[:3]) + "\n" + SESSION[3][:40])
        self.assertEqual(turns(read_lines(str(self.path)))[0].tools, [])


class Delivery(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "session.jsonl"
        self.received = received = []
        self.status = status = [200]

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if status[0] == 200:
                    received.append((self.path, self.headers["Authorization"], body))
                self.send_response(status[0])
                self.end_headers()

            def log_message(self, *_):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def export(self, event="Stop", **extra):
        env = {**{key: value for key, value in os.environ.items() if not key.startswith("CLAUDE_CODE_")}, **extra, "PYTHONPATH": str(ROOT), "CLAUDE_PLUGIN_DATA": self.directory.name + "/state",
               "CLAUDE_PLUGIN_OPTION_LENS_URL": f"http://127.0.0.1:{self.server.server_port}",
               "CLAUDE_PLUGIN_OPTION_LENS_KEY": "sk-test"}
        payload = {"session_id": "s1", "transcript_path": str(self.path), "hook_event_name": event}
        subprocess.run([sys.executable, "-m", "lens_claude_code", "export"], input=json.dumps(payload).encode(),
                       env=env, check=True, timeout=60)

    def names(self):
        return [span["name"] for _, _, body in self.received
                for span in body["resourceSpans"][0]["scopeSpans"][0]["spans"]]

    def test_each_turn_is_sent_once_with_the_key(self):
        self.path.write_text("\n".join(SESSION[:7]) + "\n")
        self.export()
        self.export()
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0][:2], ("/v1/traces", "Bearer sk-test"))
        self.assertEqual(len(self.names()), 4)

    def test_nothing_is_sent_while_native_trace_export_is_on(self):
        self.path.write_text("\n".join(SESSION[:7]) + "\n")
        self.export(CLAUDE_CODE_ENABLE_TELEMETRY="1", CLAUDE_CODE_ENHANCED_TELEMETRY_BETA="1")
        self.assertEqual(self.received, [])
        self.export()
        self.assertEqual(len(self.names()), 4)

    def test_a_rejected_turn_is_sent_again_on_the_next_hook(self):
        self.path.write_text("\n".join(SESSION[:7]) + "\n")
        self.status[0] = 500
        self.export()
        self.assertIn("HTTP 500", (Path(self.directory.name) / "state" / "last_error.txt").read_text())
        self.status[0] = 200
        self.export()
        self.assertEqual(len(self.names()), 4)
        self.assertFalse((Path(self.directory.name) / "state" / "last_error.txt").exists())


if __name__ == "__main__":
    unittest.main()
