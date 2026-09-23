// 后端交互客户端：SSE 流式（POST 用 fetch + ReadableStream，EventSource 仅支持 GET）
import type {
  ConversationDetail,
  ConversationSummary,
  FunnelStats,
  ProfilePreferences,
  SSEEvent,
} from './types'

const PROFILE_ID_STORAGE_KEY = 'shoplens.profile_id'
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
let volatileProfileId: string | null = null

/** Return one anonymous browser profile ID, replacing absent or malformed storage values. */
export function getProfileId(): string {
  if (volatileProfileId) return volatileProfileId
  let stored: string | null = null
  try {
    stored = window.localStorage.getItem(PROFILE_ID_STORAGE_KEY)
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
  if (stored && UUID_PATTERN.test(stored)) {
    volatileProfileId = stored
    return stored
  }

  const profileId = crypto.randomUUID()
  volatileProfileId = profileId
  try {
    window.localStorage.setItem(PROFILE_ID_STORAGE_KEY, profileId)
  } catch {
    // Continue with the generated ID for this page even when persistence is blocked.
  }
  return profileId
}

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
  return postSSE('/api/chat', { message, thread_id: threadId, profile_id: getProfileId() }, onEvent)
}

export function resumeStream(
  threadId: string,
  confirm: boolean,
  onEvent: (ev: SSEEvent) => void,
  skuId?: string,
  platform?: string,
): Promise<void> {
  return postSSE('/api/resume', {
    thread_id: threadId,
    confirm,
    sku_id: skuId,
    platform,
    profile_id: getProfileId(),
  }, onEvent)
}

export async function recordEvent(
  threadId: string,
  eventType: 'impression' | 'click' | 'order',
  skuId?: string,
): Promise<void> {
  await fetch('/api/conversion', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      thread_id: threadId,
      profile_id: getProfileId(),
      event_type: eventType,
      sku_id: skuId ?? null,
    }),
  })
}

export async function fetchFunnel(): Promise<FunnelStats> {
  const params = new URLSearchParams({ profile_id: getProfileId() })
  const resp = await fetch(`/api/funnel?${params}`)
  if (!resp.ok) throw new Error(`漏斗统计请求失败：${resp.status}`)
  return resp.json()
}

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({ profile_id: getProfileId() })
  const resp = await fetch(`/api/conversations?${params}`)
  if (!resp.ok) throw new Error(`会话列表加载失败：${resp.status}`)
  return resp.json()
}

export async function fetchConversation(threadId: string): Promise<ConversationDetail> {
  const params = new URLSearchParams({ profile_id: getProfileId() })
  const resp = await fetch(`/api/conversations/${threadId}?${params}`)
  if (!resp.ok) throw new Error(`会话详情加载失败：${resp.status}`)
  return resp.json()
}

export async function deleteConversation(threadId: string): Promise<void> {
  const params = new URLSearchParams({ profile_id: getProfileId() })
  const resp = await fetch(`/api/conversations/${threadId}?${params}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error(`会话删除失败：${resp.status}`)
}

export async function fetchProfilePreferences(): Promise<ProfilePreferences> {
  const params = new URLSearchParams({ profile_id: getProfileId() })
  const resp = await fetch(`/api/profile/preferences?${params}`)
  if (!resp.ok) throw new Error(`偏好记忆加载失败：${resp.status}`)
  const data = (await resp.json()) as { profile_preferences?: ProfilePreferences }
  return data.profile_preferences ?? {}
}

// 熔断演示：注入/清除某个 mock 源的故障（rate 0 清除，1 表示 100% 失败）
export async function setFailRate(source: string, rate: number): Promise<void> {
  await fetch('/api/debug/failrate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, rate }),
  })
}

// 熔断演示：查询各源熔断器当前状态（closed/open/half_open）
export async function fetchBreakers(): Promise<Record<string, string>> {
  const resp = await fetch('/api/debug/breakers')
  if (!resp.ok) throw new Error(`熔断状态查询失败：${resp.status}`)
  const data = (await resp.json()) as { sources: Record<string, string> }
  return data.sources ?? {}
}
