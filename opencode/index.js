import { readConfig } from "./src/config.js"
import { report, send } from "./src/export.js"
import { Recorder } from "./src/recorder.js"
import { payload } from "./src/spans.js"
import { responseId } from "./src/response.js"

const version = "0.1.1"

export default {
  id: "litellm-lens",
  async setup(context) {
    const config = readConfig(context.options, process.env)
    if (!config) return
    const deliveries = new Set()
    const recorder = new Recorder({
      now: Date.now,
      onTurn(turn) {
        const delivery = turn.settled
          .then(() => send(config, payload(turn, { version, opencode: context.app?.version, limit: config.limit })))
          .catch((error) => report(error.message))
          .finally(() => deliveries.delete(delivery))
        deliveries.add(delivery)
      },
    })
    const hooks = await Promise.all([
      context.session.hook("context", (input) => recorder.context(input)),
      context.session.hook("http.request", (input) => recorder.request(input.request)),
      context.session.hook("http.response", (input) =>
        recorder.response({
          sessionID: input.sessionID,
          kind: input.kind,
          model: input.model,
          request: input.request,
          ok: input.response.ok,
          callId: input.response.headers.get("x-litellm-call-id") ?? undefined,
          responseId: input.response.ok ? responseId(input.response) : Promise.resolve(undefined),
        }),
      ),
    ])
    const controller = new AbortController()
    const events = (async () => {
      try {
        for await (const event of context.event.subscribe({ signal: controller.signal })) recorder.event(event)
      } catch (error) {
        if (!controller.signal.aborted && config.debug) console.error(`[litellm-lens] ${error.message}`)
      }
    })()
    return async () => {
      controller.abort()
      await Promise.all(hooks.map((hook) => hook.dispose()))
      await events
      recorder.close()
      await Promise.all(deliveries)
    }
  },
}
