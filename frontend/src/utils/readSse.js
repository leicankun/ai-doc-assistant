export async function readSse(response, onEvent) {
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('无效的流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finished = false
  try {
    while (!finished) {
      const { value, done } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let separator
      while (!finished && (separator = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, separator.index)
        buffer = buffer.slice(separator.index + separator[0].length)
        const data = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data)
        if (event.type === 'error') throw new Error(event.message || '生成失败')
        onEvent(event)
        finished = event.type === 'done'
      }
      if (done) break
    }
    if (!finished) throw new Error('连接提前结束，回答未完成')
  } finally {
    try { await reader.cancel() } catch { /* Already closed. */ }
    reader.releaseLock()
  }
}
