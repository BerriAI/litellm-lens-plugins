const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : undefined)

export function readConfig(options = {}, env = {}) {
  const url = text(options.url) ?? text(env.LITELLM_LENS_OPENCODE_URL)
  const key = text(options.apiKey) ?? text(env.LITELLM_LENS_OPENCODE_API_KEY)
  if (!url || !key) return undefined
  const base = url.replace(/\/+$/, "").replace(/\/v1(\/traces)?$/, "")
  return {
    endpoint: `${base}/v1/traces`,
    key,
    limit: Number(options.maxContentLength ?? env.LITELLM_LENS_OPENCODE_MAX_CONTENT_LENGTH) || 200_000,
    debug: Boolean(options.debug ?? text(env.LITELLM_LENS_OPENCODE_DEBUG)),
    agent: text(options.agent) ?? text(env.LITELLM_LENS_OPENCODE_AGENT) ?? "opencode",
  }
}

export function missing(options = {}, env = {}) {
  const unset = [
    !(text(options.url) ?? text(env.LITELLM_LENS_OPENCODE_URL)) && "LITELLM_LENS_OPENCODE_URL",
    !(text(options.apiKey) ?? text(env.LITELLM_LENS_OPENCODE_API_KEY)) && "LITELLM_LENS_OPENCODE_API_KEY",
  ].filter(Boolean)
  return `not recording: ${unset.join(" and ")} ${unset.length > 1 ? "are" : "is"} not set in OpenCode's background service. Run \`opencode service restart\` from a terminal where both are set`
}
