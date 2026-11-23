import { appendFile, mkdir } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, join } from "node:path"

const waits = [500, 2000]

export async function send(config, body, { post = fetch, sleep = (ms) => new Promise((done) => setTimeout(done, ms)) } = {}) {
  for (let attempt = 0; ; attempt += 1) {
    const result = await post(config.endpoint, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${config.key}` },
      body: JSON.stringify(body),
    }).then(
      (response) => ({ status: response.status }),
      (error) => ({ error }),
    )
    if (result.status >= 200 && result.status < 300) return
    const retry = result.error || result.status >= 500 || result.status === 429
    if (!retry || attempt >= waits.length) {
      throw new Error(`trace not sent to ${config.endpoint}: ${result.error?.message ?? `HTTP ${result.status}`}`)
    }
    await sleep(waits[attempt])
  }
}

export const failureLog = (env = process.env) =>
  join(env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "litellm-lens", "opencode.log")

// The background service discards stderr, so what the user must know is also written to a file.
export async function report(message, { file = failureLog(), now = new Date() } = {}) {
  console.error(`[litellm-lens] ${message}`)
  await mkdir(dirname(file), { recursive: true })
    .then(() => appendFile(file, `${now.toISOString()} ${message}\n`))
    .catch(() => {})
}
