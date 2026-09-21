// 后端交互客户端：SSE 流式（POST 用 fetch + ReadableStream，EventSource 仅支持 GET）
import type { FunnelStats, SSEEvent } from './types'

function parseFrame(frame: string): SSEEvent | null {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return { event, data: JSON.parse(data) }
  } catch {
    return { event, data: { raw: data } }
  }
}

async function postSSE(
  url: string,
  payload: unknown,
  onEvent: (ev: SSEEvent) => void,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok || !resp.body) throw new Error(`请求失败：${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const ev = parseFrame(frame)
      if (ev) onEvent(ev)
    }
  }
}

export function chatStream(
  threadId: string,
  message: string,
  onEvent: (ev: SSEEvent) => void,
): Promise<void> {
  return postSSE('/api/chat', { message, thread_id: threadId }, onEvent)
}

export function resumeStream(
  threadId: string,
  confirm: boolean,
  onEvent: (ev: SSEEvent) => void,
  skuId?: string,
  platform?: string,
): Promise<void> {
  return postSSE('/api/resume', { thread_id: threadId, confirm, sku_id: skuId, platform }, onEvent)
}

export async function recordEvent(
  threadId: string,
  eventType: 'impression' | 'click' | 'order',
  skuId?: string,
): Promise<void> {
  await fetch('/api/conversion', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, event_type: eventType, sku_id: skuId ?? null }),
  })
}

export async function fetchFunnel(): Promise<FunnelStats> {
  const resp = await fetch('/api/funnel')
  if (!resp.ok) throw new Error(`漏斗统计请求失败：${resp.status}`)
  return resp.json()
}