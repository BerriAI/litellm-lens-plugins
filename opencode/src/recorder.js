import { randomBytes } from "node:crypto"

const settleWithin = 3000
const turnEnds = new Set(["session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"])
const id = (bytes) => randomBytes(bytes).toString("hex")

export class Recorder {
  #sessions = new Map()
  #requests = new WeakMap()
  #now
  #onTurn

  constructor({ now, onTurn }) {
    this.#now = now
    this.#onTurn = onTurn
  }

  context(input) {
    this.#session(input.sessionID).context = {
      system: structuredClone(input.system),
      messages: structuredClone(input.messages),
    }
  }

  request(request) {
    this.#requests.set(request, this.#now())
  }

  response(response) {
    const session = this.#session(response.sessionID)
    const call = {
      kind: response.kind,
      model: response.model,
      start: this.#requests.get(response.request) ?? this.#now(),
      end: this.#now(),
      callId: response.callId,
      responseId: undefined,
    }
    call.settled = response.responseId.then((value) => {
      call.responseId = value
    })
    if (!response.ok) return
    if (response.kind === "primary") session.responses.push(call)
    else this.#turn(session).other.push({ ...call, spanId: id(8), call })
  }

  event(event) {
    const data = event.data
    if (!event.type.startsWith("session.") || !data?.sessionID) return
    const session = this.#session(data.sessionID)
    const step = () => this.#turn(session).steps.find((item) => item.id === data.assistantMessageID)
    switch (event.type) {
      case "session.inbox.enqueued":
        if (data.item?.type === "user") session.inbox.set(data.inboxID, data.item.payload?.text ?? "")
        return
      case "session.inbox.delivered":
        if (!session.inbox.has(data.inboxID)) return
        this.#turn(session).prompts.push(session.inbox.get(data.inboxID))
        session.inbox.delete(data.inboxID)
        return
      case "session.execution.started":
        this.#turn(session)
        return
      case "session.step.started":
        this.#turn(session).steps.push({
          id: data.assistantMessageID,
          spanId: id(8),
          start: data.started ?? this.#now(),
          model: data.model,
          agent: data.agent,
          context: session.context,
          call: session.responses.shift(),
          text: [],
          reasoning: [],
          tools: [],
        })
        return
      case "session.text.ended":
        step()?.text.push(data.text)
        return
      case "session.reasoning.ended":
        step()?.reasoning.push(data.text)
        return
      case "session.tool.input.started":
        this.#tool(session, data).name = data.name
        return
      case "session.tool.called": {
        const tool = this.#tool(session, data)
        tool.input = data.input
        tool.start = this.#now()
        step()?.tools.push(tool)
        return
      }
      case "session.tool.success":
      case "session.tool.failed": {
        const tool = this.#tool(session, data)
        tool.end = this.#now()
        tool.output = data.content
        tool.error = event.type === "session.tool.failed" ? (data.error ?? { message: "Tool failed" }) : undefined
        return
      }
      case "session.step.ended":
      case "session.step.failed": {
        const current = step()
        if (!current) return
        current.call ??= session.responses.shift()
        current.end = this.#now()
        current.tokens = data.tokens
        current.finish = data.rawFinish ?? data.finish
        current.error = data.error
        return
      }
      default:
        if (turnEnds.has(event.type)) this.#finish(session, event.type.split(".").at(-1), data.error)
    }
  }

  close() {
    for (const session of this.#sessions.values()) this.#finish(session, "interrupted")
  }

  #session(sessionID) {
    if (!this.#sessions.has(sessionID)) {
      this.#sessions.set(sessionID, { id: sessionID, inbox: new Map(), responses: [], context: undefined, turn: undefined })
    }
    return this.#sessions.get(sessionID)
  }

  #turn(session) {
    session.turn ??= {
      sessionID: session.id,
      traceId: id(16),
      spanId: id(8),
      start: this.#now(),
      prompts: [],
      steps: [],
      tools: new Map(),
      other: [],
    }
    return session.turn
  }

  #tool(session, data) {
    const tools = this.#turn(session).tools
    if (!tools.has(data.id)) tools.set(data.id, { id: data.id, spanId: id(8), name: "tool", start: this.#now() })
    return tools.get(data.id)
  }

  #finish(session, outcome, error) {
    const turn = session.turn
    if (!turn) return
    session.turn = undefined
    session.responses.length = 0
    turn.end = this.#now()
    turn.outcome = outcome
    turn.error = error
    const calls = [...turn.steps, ...turn.other].flatMap((item) => (item.call ? [item.call.settled] : []))
    turn.settled = Promise.race([Promise.all(calls), new Promise((done) => setTimeout(done, settleWithin).unref?.())])
    this.#onTurn(turn)
  }
}
