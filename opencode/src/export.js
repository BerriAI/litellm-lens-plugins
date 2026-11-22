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
      throw new Error(`Lens rejected the trace: ${result.error?.message ?? `HTTP ${result.status}`}`)
    }
    await sleep(waits[attempt])
  }
}
