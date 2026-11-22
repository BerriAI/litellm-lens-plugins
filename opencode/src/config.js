const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : undefined)

export function readConfig(options = {}, env = {}) {
  const url = text(options.url) ?? text(env.LITELLM_LENS_OPENCODE_URL)
  const key = text(options.apiKey) ?? text(env.LITELLM_LENS_OPENCODE_API_KEY)
  if (!url || !key) return undefined
  const base = url.replace(/\/+$/, "")
  return {
    endpoint: base.endsWith("/v1/traces") ? base : `${base}/v1/traces`,
    key,
    limit: Number(options.maxContentLength ?? env.LITELLM_LENS_OPENCODE_MAX_CONTENT_LENGTH) || 200_000,
    debug: Boolean(options.debug ?? text(env.LITELLM_LENS_OPENCODE_DEBUG)),
  }
}
