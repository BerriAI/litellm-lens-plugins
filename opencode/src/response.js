const pattern = /"id"\s*:\s*"([^"]+)"/
const scanned = 16_384

export async function responseId(response) {
  if (!response.body) return undefined
  const reader = response.clone().body.getReader()
  const decoder = new TextDecoder()
  let seen = ""
  try {
    while (seen.length < scanned) {
      const { done, value } = await reader.read()
      if (done) break
      seen += decoder.decode(value, { stream: true })
      const match = pattern.exec(seen)
      if (match) return match[1]
    }
    return undefined
  } catch {
    return undefined
  } finally {
    reader.cancel().catch(() => {})
  }
}
