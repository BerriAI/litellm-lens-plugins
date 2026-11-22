const scope = "berriai.lens.opencode"
const agent = "opencode"

const value = (item) => {
  if (typeof item === "boolean") return { boolValue: item }
  if (Number.isInteger(item)) return { intValue: String(item) }
  if (typeof item === "number") return { doubleValue: item }
  return { stringValue: String(item) }
}

const attributes = (values) =>
  Object.entries(values)
    .filter(([, item]) => item !== undefined && item !== null && item !== "")
    .map(([key, item]) => ({ key, value: value(item) }))

const nanos = (ms) => `${Math.round(ms)}000000`

const clip = (text, limit) =>
  text.length > limit ? `${text.slice(0, limit)}\n[truncated ${text.length - limit} characters]` : text

const display = (item) => (typeof item === "string" ? item : JSON.stringify(item))

const resultText = (result) => {
  if (result?.type === "text" && typeof result.value === "string") return result.value
  if (Array.isArray(result)) return result.map((part) => (part?.type === "text" ? part.text : display(part))).join("\n")
  return display(result ?? "")
}

function part(item) {
  switch (item.type) {
    case "text":
      return { type: "text", content: item.text }
    case "reasoning":
      return { type: "reasoning", content: item.text }
    case "tool-call":
      return { type: "tool_call", id: item.id, name: item.name, arguments: item.input }
    case "tool-result":
      return { type: "tool_call_response", id: item.id, response: resultText(item.result) }
    default:
      return { type: item.type ?? "unknown" }
  }
}

const messages = (list, limit) =>
  clip(JSON.stringify(list.map((message) => ({ role: message.role, parts: [].concat(message.content ?? []).map(part) }))), limit)

const failure = (error) => (error ? { code: 2, message: String(error.message ?? display(error)).slice(0, 1000) } : undefined)

function span(turn, fields, values) {
  return {
    traceId: turn.traceId,
    spanId: fields.spanId,
    parentSpanId: fields.parentSpanId,
    name: fields.name,
    kind: fields.kind ?? 1,
    startTimeUnixNano: nanos(fields.start),
    endTimeUnixNano: nanos(Math.max(fields.end ?? turn.end, fields.start)),
    attributes: attributes({
      "session.id": turn.sessionID,
      "gen_ai.conversation.id": turn.sessionID,
      "gen_ai.agent.name": agent,
      ...values,
    }),
    ...(fields.status ? { status: fields.status } : {}),
  }
}

function modelValues(model, call) {
  return {
    "gen_ai.operation.name": "chat",
    "gen_ai.provider.name": model?.providerID,
    "gen_ai.request.model": model?.id,
    "gen_ai.response.id": call?.responseId,
    "litellm.call_id": call?.callId,
  }
}

function stepSpan(turn, step, limit) {
  const output = [
    ...step.reasoning.map((text) => ({ type: "reasoning", text })),
    ...step.text.map((text) => ({ type: "text", text })),
    ...step.tools.map((tool) => ({ type: "tool-call", id: tool.id, name: tool.name, input: tool.input })),
  ]
  const tokens = step.tokens
  return span(
    turn,
    {
      spanId: step.spanId,
      parentSpanId: turn.spanId,
      name: `chat ${step.model?.id ?? "model"}`,
      kind: 3,
      start: step.start,
      end: step.end,
      status: failure(step.error),
    },
    {
      ...modelValues(step.model, step.call),
      "opencode.agent": step.agent,
      "gen_ai.response.finish_reasons": step.finish,
      "gen_ai.usage.input_tokens": tokens?.input,
      "gen_ai.usage.output_tokens": tokens?.output,
      "gen_ai.usage.cache_read.input_tokens": tokens?.cache?.read,
      "gen_ai.usage.cache_creation.input_tokens": tokens?.cache?.write,
      "opencode.usage.reasoning_tokens": tokens?.reasoning,
      "gen_ai.system_instructions": step.context?.system.length
        ? clip(JSON.stringify(step.context.system.map((item) => ({ type: "text", content: item.text }))), limit)
        : undefined,
      "gen_ai.input.messages": step.context ? messages(step.context.messages, limit) : undefined,
      "gen_ai.output.messages": output.length ? messages([{ role: "assistant", content: output }], limit) : undefined,
    },
  )
}

function toolSpan(turn, tool, limit) {
  return span(
    turn,
    {
      spanId: tool.spanId,
      parentSpanId: turn.spanId,
      name: `execute_tool ${tool.name}`,
      start: tool.start,
      end: tool.end,
      status: failure(tool.error),
    },
    {
      "gen_ai.operation.name": "execute_tool",
      "gen_ai.tool.name": tool.name,
      "gen_ai.tool.call.id": tool.id,
      "gen_ai.tool.call.arguments": tool.input === undefined ? undefined : clip(JSON.stringify(tool.input), limit),
      "gen_ai.tool.call.result": tool.output === undefined ? undefined : clip(resultText(tool.output), limit),
    },
  )
}

function otherSpan(turn, call) {
  return span(
    turn,
    {
      spanId: call.spanId,
      parentSpanId: turn.spanId,
      name: `chat ${call.model?.id ?? "model"}`,
      kind: 3,
      start: call.start,
      end: call.end,
    },
    { ...modelValues(call.model, call.call), "opencode.request.kind": call.kind },
  )
}

export function payload(turn, { version, opencode, limit }) {
  const reply = turn.steps.flatMap((step) => step.text).at(-1)
  const root = span(
    turn,
    {
      spanId: turn.spanId,
      name: `invoke_agent ${agent}`,
      start: turn.start,
      end: turn.end,
      status: failure(turn.error),
    },
    {
      "gen_ai.operation.name": "invoke_agent",
      "opencode.turn.outcome": turn.outcome,
      "gen_ai.input.messages": turn.prompts.length
        ? messages([{ role: "user", content: turn.prompts.map((text) => ({ type: "text", text })) }], limit)
        : undefined,
      "gen_ai.output.messages": reply
        ? messages([{ role: "assistant", content: [{ type: "text", text: reply }] }], limit)
        : undefined,
    },
  )
  return {
    resourceSpans: [
      {
        resource: { attributes: attributes({ "service.name": agent, "service.version": opencode }) },
        scopeSpans: [
          {
            scope: { name: scope, version },
            spans: [
              root,
              ...turn.steps.map((step) => stepSpan(turn, step, limit)),
              ...[...turn.tools.values()].map((tool) => toolSpan(turn, tool, limit)),
              ...turn.other.map((call) => otherSpan(turn, call)),
            ],
          },
        ],
      },
    ],
  }
}
