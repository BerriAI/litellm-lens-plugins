import assert from "node:assert/strict"
import { test } from "node:test"
import { readConfig } from "../src/config.js"
import { mkdtemp, readFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { failureLog, report, send } from "../src/export.js"
import { Recorder } from "../src/recorder.js"
import { responseId } from "../src/response.js"
import { payload } from "../src/spans.js"

const sessionID = "ses_1"
const model = { id: "claude-haiku-4-5", providerID: "anthropic" }
const event = (type, data = {}) => ({ type, data: { sessionID, ...data } })

function record(play) {
  const turns = []
  let clock = 1000
  const recorder = new Recorder({ now: () => (clock += 10), onTurn: (turn) => turns.push(turn) })
  play(recorder)
  return turns
}

const call = (kind, callId, id, ok = true) => ({
  sessionID,
  kind,
  model,
  request: {},
  ok,
  callId,
  responseId: Promise.resolve(id),
})

function toolTurn(recorder) {
  recorder.event(event("session.inbox.enqueued", { inboxID: "in_1", item: { type: "user", payload: { text: "read it" } } }))
  recorder.event(event("session.execution.started"))
  recorder.event(event("session.inbox.delivered", { inboxID: "in_1" }))
  recorder.context({
    sessionID,
    system: [{ type: "text", text: "be brief" }],
    messages: [{ role: "user", content: [{ type: "text", text: "read it" }] }],
  })
  recorder.response(call("title", "call_title", "msg_title"))
  recorder.response(call("primary", "call_1", "msg_1"))
  recorder.event(event("session.step.started", { assistantMessageID: "a1", model, agent: "build", started: 2000 }))
  recorder.event(event("session.tool.input.started", { assistantMessageID: "a1", id: "tool_1", name: "read" }))
  recorder.event(event("session.tool.called", { assistantMessageID: "a1", id: "tool_1", input: { path: "note.txt" } }))
  recorder.event(event("session.tool.success", { id: "tool_1", content: [{ type: "text", text: "secret" }] }))
  recorder.event(
    event("session.step.ended", {
      assistantMessageID: "a1",
      rawFinish: "tool_use",
      tokens: { input: 3, output: 54, reasoning: 0, cache: { read: 7, write: 9 } },
    }),
  )
  recorder.response(call("primary", "call_2", "msg_2"))
  recorder.event(event("session.step.started", { assistantMessageID: "a2", model, agent: "build", started: 3000 }))
  recorder.event(event("session.text.ended", { assistantMessageID: "a2", text: "it says secret" }))
  recorder.event(event("session.step.ended", { assistantMessageID: "a2", rawFinish: "end_turn", tokens: { input: 6, output: 20 } }))
  recorder.event(event("session.execution.succeeded"))
}

async function spans(play, limit = 1000) {
  const [turn] = record(play)
  await turn.settled
  const body = payload(turn, { version: "0.1.0", opencode: "2.3.2", limit })
  return body.resourceSpans[0].scopeSpans[0].spans.map((span) => ({
    ...span,
    values: Object.fromEntries(span.attributes.map(({ key, value }) => [key, Object.values(value)[0]])),
  }))
}

test("a turn becomes one trace whose model calls carry the ids the gateway bills by", async () => {
  const all = await spans(toolTurn)
  const [root, first, second, tool, title] = all
  assert.equal(new Set(all.map((span) => span.traceId)).size, 1)
  assert.deepEqual(
    all.slice(1).map((span) => span.parentSpanId),
    Array(4).fill(root.spanId),
  )
  assert.deepEqual(
    [first, second, title].map((span) => [span.values["gen_ai.response.id"], span.values["litellm.call_id"]]),
    [
      ["msg_1", "call_1"],
      ["msg_2", "call_2"],
      ["msg_title", "call_title"],
    ],
  )
  assert.equal(title.values["opencode.request.kind"], "title")
  assert.equal(first.values["gen_ai.usage.input_tokens"], "3")
  assert.equal(first.values["gen_ai.usage.cache_creation.input_tokens"], "9")
  assert.equal(tool.values["gen_ai.tool.call.id"], "tool_1")
  assert.equal(all.every((span) => span.values["session.id"] === sessionID), true)
})

test("prompts, replies, system prompt and tool content are recorded", async () => {
  const [root, first, second, tool] = await spans(toolTurn)
  assert.deepEqual(JSON.parse(root.values["gen_ai.input.messages"]), [
    { role: "user", parts: [{ type: "text", content: "read it" }] },
  ])
  assert.deepEqual(JSON.parse(root.values["gen_ai.output.messages"]), [
    { role: "assistant", parts: [{ type: "text", content: "it says secret" }] },
  ])
  assert.deepEqual(JSON.parse(first.values["gen_ai.system_instructions"]), [{ type: "text", content: "be brief" }])
  assert.deepEqual(JSON.parse(first.values["gen_ai.output.messages"])[0].parts, [
    { type: "tool_call", id: "tool_1", name: "read", arguments: { path: "note.txt" } },
  ])
  assert.equal(JSON.parse(second.values["gen_ai.output.messages"])[0].parts[0].content, "it says secret")
  assert.equal(tool.values["gen_ai.tool.call.arguments"], '{"path":"note.txt"}')
  assert.equal(tool.values["gen_ai.tool.call.result"], "secret")
})

test("a failed response is not billed to the step that follows it", async () => {
  const [, step] = await spans((recorder) => {
    recorder.event(event("session.execution.started"))
    recorder.response(call("primary", "call_bad", undefined, false))
    recorder.response(call("primary", "call_good", "msg_good"))
    recorder.event(event("session.step.started", { assistantMessageID: "a1", model }))
    recorder.event(event("session.step.ended", { assistantMessageID: "a1" }))
    recorder.event(event("session.execution.succeeded"))
  })
  assert.equal(step.values["gen_ai.response.id"], "msg_good")
})

test("failures mark the tool, the step and the turn as errors", async () => {
  const [root, step, tool] = await spans((recorder) => {
    recorder.event(event("session.execution.started"))
    recorder.event(event("session.step.started", { assistantMessageID: "a1", model }))
    recorder.event(event("session.tool.called", { assistantMessageID: "a1", id: "tool_1", input: {} }))
    recorder.event(event("session.tool.failed", { id: "tool_1", error: { message: "denied" } }))
    recorder.event(event("session.step.failed", { assistantMessageID: "a1", error: { message: "overloaded" } }))
    recorder.event(event("session.execution.failed", { error: { message: "overloaded" } }))
  })
  assert.deepEqual([root, step, tool].map((span) => span.status), [
    { code: 2, message: "overloaded" },
    { code: 2, message: "overloaded" },
    { code: 2, message: "denied" },
  ])
  assert.equal(root.values["opencode.turn.outcome"], "failed")
})

test("closing sends an unfinished turn as interrupted, and each turn gets its own trace", async () => {
  const turns = record((recorder) => {
    toolTurn(recorder)
    recorder.event(event("session.execution.started"))
    recorder.close()
  })
  assert.deepEqual(turns.map((turn) => turn.outcome), ["succeeded", "interrupted"])
  assert.notEqual(turns[0].traceId, turns[1].traceId)
})

test("each model call carries only the messages added since the previous reply", async () => {
  const [, step] = await spans((recorder) => {
    recorder.event(event("session.execution.started"))
    recorder.context({
      sessionID,
      system: [],
      messages: [
        { role: "user", content: [{ type: "text", text: "first" }] },
        { role: "assistant", content: [{ type: "tool-call", id: "tool_1", name: "read", input: {} }] },
        { role: "tool", content: [{ type: "tool-result", id: "tool_1", result: { type: "text", value: "secret" } }] },
        { role: "user", content: [{ type: "text", text: "second" }] },
      ],
    })
    recorder.event(event("session.step.started", { assistantMessageID: "a1", model }))
    recorder.event(event("session.step.ended", { assistantMessageID: "a1" }))
    recorder.event(event("session.execution.succeeded"))
  })
  assert.deepEqual(JSON.parse(step.values["gen_ai.input.messages"]), [
    { role: "tool", parts: [{ type: "tool_call_response", id: "tool_1", response: "secret" }] },
    { role: "user", parts: [{ type: "text", content: "second" }] },
  ])
})

test("the session title call carries the title it produced and no messages", async () => {
  const title = (await spans((recorder) => {
    recorder.event(event("session.execution.started"))
    recorder.response(call("title", "call_title", "msg_title"))
    recorder.event(event("session.renamed", { title: "Reading a note" }))
    recorder.event(event("session.execution.succeeded"))
  })).at(-1)
  assert.equal(title.values["opencode.session.title"], "Reading a note")
  assert.equal(title.values["gen_ai.input.messages"], undefined)
  assert.equal(title.values["gen_ai.output.messages"], undefined)
})

test("a subagent's turn joins its parent's trace under the tool call that started it", async () => {
  const child = "ses_child"
  const [parent, sub] = record((recorder) => {
    recorder.event(event("session.execution.started"))
    recorder.event(event("session.step.started", { assistantMessageID: "a1", model }))
    recorder.event(event("session.tool.called", { assistantMessageID: "a1", id: "tool_1", input: {} }))
    recorder.event({ type: "session.created", data: { sessionID: child, parentID: sessionID } })
    recorder.event(event("session.tool.progress", { id: "tool_1", metadata: { sessionID: child } }))
    recorder.event({ type: "session.execution.started", data: { sessionID: child } })
    recorder.event(event("session.execution.succeeded"))
    recorder.event({ type: "session.execution.succeeded", data: { sessionID: child } })
  })
  await sub.settled
  const [root] = payload(sub, { version: "0.1.0", opencode: "2.3.2", limit: 1000 }).resourceSpans[0].scopeSpans[0].spans
  const values = Object.fromEntries(root.attributes.map(({ key, value }) => [key, Object.values(value)[0]]))
  assert.equal(root.traceId, parent.traceId)
  assert.equal(root.parentSpanId, parent.tools.get("tool_1").spanId)
  assert.equal(values["session.id"], sessionID)
  assert.equal(values["opencode.subagent.session.id"], child)
})

test("content longer than the limit is cut and says how much was dropped", async () => {
  const [, , , tool] = await spans((recorder) => {
    toolTurn(recorder)
  }, 3)
  assert.equal(tool.values["gen_ai.tool.call.result"], "sec\n[truncated 3 characters]")
})

test("the plugin stays off until both a URL and a key are set", () => {
  assert.equal(readConfig({}, {}), undefined)
  assert.equal(readConfig({ url: "http://gateway" }, {}), undefined)
  assert.equal(readConfig({}, { LITELLM_LENS_OPENCODE_API_KEY: "sk-1" }), undefined)
  assert.equal(readConfig({ url: "http://gateway/", apiKey: "sk-1" }, {}).endpoint, "http://gateway/v1/traces")
  const fromEnv = readConfig({}, { LITELLM_LENS_OPENCODE_URL: "http://lens/v1/traces", LITELLM_LENS_OPENCODE_API_KEY: "sk-2" })
  assert.deepEqual([fromEnv.endpoint, fromEnv.key], ["http://lens/v1/traces", "sk-2"])
})

test("a model base URL ending in /v1 posts to the same traces endpoint", () => {
  for (const url of ["http://gateway/v1", "http://gateway/v1/", "http://gateway/proxy/v1", "http://gateway/v1/traces/"]) {
    const base = url.includes("proxy") ? "http://gateway/proxy" : "http://gateway"
    assert.equal(readConfig({ url, apiKey: "sk-1" }, {}).endpoint, `${base}/v1/traces`)
  }
})

test("the response id is read from a stream without consuming the response", async () => {
  const stream = 'event: message_start\ndata: {"type":"message_start","message":{"model":"m","id":"msg_9"}}\n\n'
  const response = new Response(stream)
  assert.equal(await responseId(response), "msg_9")
  assert.equal(await response.text(), stream)
  assert.equal(await responseId(new Response("no identifier here")), undefined)
})

test("sending retries server errors and gives up on a rejected key", async () => {
  const config = { endpoint: "http://gateway/v1/traces", key: "sk-1" }
  const statuses = [503, 200]
  const seen = []
  const post = async (url, init) => {
    seen.push([url, init.headers.authorization])
    return { status: statuses.shift() }
  }
  await send(config, {}, { post, sleep: async () => {} })
  assert.deepEqual(seen, Array(2).fill(["http://gateway/v1/traces", "Bearer sk-1"]))
  let attempts = 0
  const refused = async () => ((attempts += 1), { status: 401 })
  await assert.rejects(send(config, {}, { post: refused, sleep: async () => {} }), /not sent to http:\/\/gateway\/v1\/traces: HTTP 401/)
  assert.equal(attempts, 1)
})

test("a failed upload is written to the failure log", async () => {
  const file = failureLog({ XDG_STATE_HOME: await mkdtemp(join(tmpdir(), "lens-")) })
  assert.match(file, /litellm-lens\/opencode\.log$/)
  await report("trace not sent to http://gateway/v1/traces: HTTP 404", { file, now: new Date(0) })
  assert.equal(await readFile(file, "utf8"), "1970-01-01T00:00:00.000Z trace not sent to http://gateway/v1/traces: HTTP 404\n")
})
