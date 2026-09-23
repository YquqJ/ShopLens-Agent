// Pinia 会话 store：会话列表 + 每轮导购的 SSE 事件流状态机。
//
// 一轮导购 = 用户一句话 → POST /chat 返回整条 SSE 流（clarify→…→recommend→interrupt），
// 前端把 node/interrupt/done/error 四类事件逐步落到当前 Turn 上，驱动消息气泡 + 比价表 + HITL + Trace 抽屉。
import { reactive } from 'vue'
import { defineStore } from 'pinia'
import {
  chatStream,
  deleteConversation,
  fetchConversation,
  fetchConversations,
  fetchProfilePreferences,
  recordEvent,
  resumeStream,
} from '../api/client'
import type {
  ConversationDetail,
  DraftOrder,
  NormalizedItem,
  OrderResult,
  MemoryEvent,
  Preferences,
  ProfilePreferences,
  Recommendation,
  SSEEvent,
} from '../api/types'

export interface TraceNode {
  name: string
  label: string
  desc: string
  status: 'done' | 'waiting' | 'pending'
}

export interface Turn {
  id: string
  user: string
  ts: number
  clarifyQuestion: string
  agentReply: string
  preferences: Preferences | null
  normalized: NormalizedItem[]
  recommendation: Recommendation | null
  degradedSources: string[]
  draftOrder: DraftOrder | null
  order: OrderResult | null
  trace: TraceNode[]
  streaming: boolean
  awaitingHitl: boolean
  impressed: boolean
  clickedSkus: string[]
  priceRaised: boolean
  error: string
}

export interface Session {
  id: string
  title: string
  subtitle: string
  turns: Turn[]
}

// 节点名 → Trace 抽屉展示文案（对齐原型右侧 Agent Trace）
const NODE_META: Record<string, { label: string; desc: string }> = {
  clarify: { label: '澄清', desc: '提取结构化偏好 / 判断是否缺关键信息' },
  route: { label: '意图路由', desc: '判断导购 vs 其他' },
  retrieve: { label: '商品检索', desc: 'Chroma 向量召回候选 SKU' },
  compare: { label: '多源比价', desc: '并发调用 3 个 MCP 比价源' },
  normalize: { label: 'SKU 归一 + 最优解', desc: '三源报价对齐 + 综合成本最优' },
  recommend: { label: '可解释推荐', desc: '生成可溯源推荐理由' },
  confirm: { label: '下单确认', desc: 'HITL：人工确认下单' },
  lock_price: { label: '二次询价锁价', desc: '下单前重新报价锁定价格' },
  place_order: { label: '下单落库', desc: '订单写入 PostgreSQL' },
  load_memory: { label: '读取偏好记忆', desc: '读取已授权的跨会话偏好' },
  capture_memory: { label: '更新偏好记忆', desc: '仅处理用户明确授权的偏好变更' },
}

const MEMORY_KEY_LABELS: Record<string, string> = {
  preferred_colors: '颜色',
  preferred_styles: '风格',
  preferred_materials: '材质',
  avoid_preferences: '避雷项',
}

function memoryKeysLabel(keys: string[]): string {
  return keys.map((key) => MEMORY_KEY_LABELS[key] ?? '偏好').join('、')
}

function memoryEventDescription(event: MemoryEvent | undefined): string {
  if (!event) return ''
  if (event.type === 'memory_read') {
    return event.keys.length ? ` · 已读取：${memoryKeysLabel(event.keys)}` : ' · 暂无已保存偏好'
  }
  const action = event.action === 'forget' ? '已遗忘' : '已保存'
  return event.keys.length ? ` · ${action}：${memoryKeysLabel(event.keys)}` : ''
}

function newTurn(user: string, ts?: number): Turn {
  // 必须 reactive：sendMessage 手里攥着原始引用，后续 SSE 事件直接改它的字段，
  // 若为普通对象会绕过 Vue Proxy 的 set 陷阱 → 数据更新但组件不重渲染（spinner 卡死）。
  return reactive({
    id: crypto.randomUUID(),
    user,
    ts: ts ?? Date.now(),
    clarifyQuestion: '',
    agentReply: '',
    preferences: null,
    normalized: [],
    recommendation: null,
    degradedSources: [],
    draftOrder: null,
    order: null,
    trace: [],
    streaming: true,
    awaitingHitl: false,
    impressed: false,
    clickedSkus: [],
    priceRaised: false,
    error: '',
  })
}

function hydrateTurns(d: ConversationDetail): Turn[] {
  // 第一档重建：messages 按「user 开新轮」切分，assistant 文本入 clarifyQuestion/agentReply，
  // 最新一轮补挂 checkpointer 的最新结构化结果（推荐/草稿/订单，多轮历史卡片不逐轮保留）。
  const turns: Turn[] = []
  let cur: Turn | null = null
  for (const m of d.messages) {
    if (m.role === 'user') {
      cur = newTurn(m.content, m.ts != null ? m.ts * 1000 : undefined)
      cur.streaming = false
      turns.push(cur)
    } else if (cur) {
      const q = m.content
      if (q.endsWith('？') || q.endsWith('?')) cur.clarifyQuestion = q
      else cur.agentReply = q
    }
  }
  const last = turns.at(-1)
  if (last) {
    last.preferences = d.preferences && Object.keys(d.preferences).length ? d.preferences : null
    // 末轮已成交（placed/cancelled）：不还原候选卡，改由 messages 里的「已下单/未下单」摘要原位替位
    const resolved = d.order?.status === 'placed' || d.order?.status === 'cancelled'
    if (!resolved) {
      last.normalized = d.normalized ?? []
      last.recommendation = d.recommendation
      last.degradedSources = d.recommendation?.degraded_sources ?? []
      last.draftOrder = d.draft_order
      last.order = d.order
      last.priceRaised = d.price_raised
      last.awaitingHitl = d.awaiting_hitl
      last.impressed = !!d.recommendation
    }
  }
  return turns
}

function pushTrace(turn: Turn, name: string, status: TraceNode['status'], extra?: string): void {
  // 复用同名节点：interrupt 后 resume 会再发 lock_price/place_order，直接更新已存在的待执行项
  const existing = turn.trace.find((n) => n.name === name)
  const meta = NODE_META[name] ?? { label: name, desc: '' }
  const desc = extra ? `${meta.desc}${extra}` : meta.desc
  if (existing) {
    existing.status = status
    existing.desc = desc
    return
  }
  turn.trace.push({ name, label: meta.label, desc, status })
}

// 金额展示：整元不带小数（与后端 place_order 回写 messages 的摘要文案一致）
function fmtYuan(v: number | null | undefined): string {
  if (v == null) return ''
  return Number.isInteger(v) ? String(v) : v.toFixed(2)
}

// 平台展示名（与后端 place_order 回写 messages 的摘要文案保持一致）
const PLATFORM_NAME: Record<string, string> = { jd: '京东', taobao: '淘宝', pdd: '拼多多', master: '主库兜底' }

// 下单完成摘要文本：候选卡用「已下单 xx · ¥xx · 平台」替位
function orderSummary(turn: Turn): string {
  const title = turn.draftOrder?.title ?? ''
  const price = fmtYuan(turn.order?.final_price ?? turn.draftOrder?.final_price)
  const platform = turn.order?.platform ?? turn.draftOrder?.platform
  return `已下单「${title}」· ¥${price}${platform ? ` · ${PLATFORM_NAME[platform] ?? platform}` : ''}`
}

export const useChatStore = defineStore('chat', {
  state: () => {
    const first: Session = {
      id: crypto.randomUUID(),
      title: '新对话',
      subtitle: '等待输入购物需求',
      turns: [],
    }
    return {
      sessions: [first],
      activeId: first.id,
      loadedIds: [] as string[],
      profilePreferences: {} as ProfilePreferences,
      memoryEvents: [] as MemoryEvent[],
    }
  },
  getters: {
    activeSession(state): Session {
      return state.sessions.find((s) => s.id === state.activeId) ?? state.sessions[0]
    },
  },
  actions: {
    newSession(): string {
      const s: Session = { id: crypto.randomUUID(), title: '新对话', subtitle: '等待输入购物需求', turns: [] }
      this.sessions.unshift(s)
      this.activeId = s.id
      return s.id
    },
    selectSession(id: string): void {
      if (!this.sessions.some((s) => s.id === id)) return
      this.activeId = id
      void this.loadSession(id)
    },
    // 启动时从后端拉会话列表，回填左侧 rail 并加载最近一条
    async hydrate(): Promise<void> {
      try {
        await this.refreshProfilePreferences()
      } catch (e) {
        console.error('偏好记忆加载失败', e)
      }
      try {
        const list = await fetchConversations()
        if (!list.length) return
        this.sessions = list.map((s) => ({
          id: s.thread_id,
          title: s.title,
          subtitle: s.subtitle,
          turns: [],
        }))
        this.activeId = list[0].thread_id
        this.loadedIds = []
        await this.loadSession(list[0].thread_id)
      } catch (e) {
        console.error('会话列表加载失败', e)
      }
    },
    // 懒加载某会话历史（只拉一次，缓存到 loadedIds）
    async loadSession(id: string): Promise<void> {
      if (this.loadedIds.includes(id)) return
      const session = this.sessions.find((s) => s.id === id)
      if (!session) return
      try {
        const d = await fetchConversation(id)
        session.turns = hydrateTurns(d)
        this.profilePreferences = d.profile_preferences ?? {}
        this.memoryEvents = d.memory_events ?? []
        this.loadedIds.push(id)
      } catch (e) {
        console.error('会话详情加载失败', e)
      }
    },
    async refreshProfilePreferences(): Promise<void> {
      try {
        this.profilePreferences = await fetchProfilePreferences()
      } catch (e) {
        console.error('偏好记忆加载失败', e)
      }
    },
    // 删除会话：后端删行 + 本地移除；删的是当前会话则切到最近一条或新建空会话
    async deleteSession(id: string): Promise<void> {
      await deleteConversation(id)
      const wasActive = this.activeId === id
      this.sessions = this.sessions.filter((s) => s.id !== id)
      this.loadedIds = this.loadedIds.filter((x) => x !== id)
      if (wasActive) {
        if (this.sessions.length) {
          this.activeId = this.sessions[0].id
          void this.loadSession(this.sessions[0].id)
        } else {
          this.newSession()
        }
      }
    },
    async sendMessage(text: string): Promise<void> {
      const session = this.activeSession
      const turn = newTurn(text)
      session.turns.push(turn)
      if (session.turns.length === 1) session.title = text.slice(0, 18)
      await this.run(turn, () => chatStream(session.id, text, (ev) => this.handleEvent(session, turn, ev)))
    },
    async resumeHitl(turnId: string, confirm: boolean, skuId?: string, platform?: string): Promise<void> {
      const session = this.activeSession
      const turn = session.turns.find((t) => t.id === turnId)
      if (!turn) return
      turn.awaitingHitl = false
      turn.streaming = true
      await this.run(turn, () => resumeStream(session.id, confirm, (ev) => this.handleEvent(session, turn, ev), skuId, platform))
    },
    trackClick(skuId: string, turnId: string): void {
      // 每轮每 SKU 只记一次点击（点击=选中候选），避免重复点击把点击率放大失真
      const turn = this.activeSession.turns.find((t) => t.id === turnId)
      if (!turn || turn.clickedSkus.includes(skuId)) return
      turn.clickedSkus.push(skuId)
      // 漏斗「点击推荐」只统计点击👍推荐品，备选点击不计数
      if (skuId !== turn.recommendation?.items[0]?.sku_id) return
      void recordEvent(this.activeId, 'click', skuId)
    },
    async run(turn: Turn, stream: () => Promise<void>): Promise<void> {
      try {
        await stream()
      } catch (e) {
        turn.error = e instanceof Error ? e.message : String(e)
        turn.streaming = false
      }
    },
    handleEvent(session: Session, turn: Turn, ev: SSEEvent): void {
      if (ev.event === 'node') {
        this.handleNode(session, turn, ev.data)
      } else if (ev.event === 'interrupt') {
        this.handleInterrupt(turn, ev.data)
      } else if (ev.event === 'done') {
        this.handleDone(turn, ev.data)
      } else if (ev.event === 'error') {
        turn.error = String((ev.data as { message?: string }).message ?? '未知错误')
        turn.streaming = false
      }
    },
    handleNode(session: Session, turn: Turn, data: Record<string, unknown>): void {
      const node = String(data.node ?? '')
      const meta = NODE_META[node]
      if (meta) pushTrace(turn, node, 'done')
      switch (node) {
        case 'clarify': {
          turn.preferences = (data.preferences as Preferences) ?? null
          turn.clarifyQuestion = String(data.clarify_question ?? '')
          this.patchSubtitle(session, turn.preferences)
          break
        }
        case 'load_memory':
        case 'capture_memory': {
          const memoryEvents = (data.memory_events as MemoryEvent[] | undefined) ?? []
          if (memoryEvents.length) this.memoryEvents = memoryEvents
          if (memoryEvents.length) void this.refreshProfilePreferences()
          pushTrace(turn, node, 'done', memoryEventDescription(memoryEvents.at(-1)))
          break
        }
        case 'route': {
          turn.agentReply = String(data.reply ?? '')
          break
        }
        case 'retrieve': {
          const candidates = (data.candidates as unknown[]) ?? []
          pushTrace(turn, 'retrieve', 'done', ` · 召回 ${candidates.length} SKU`)
          break
        }
        case 'compare': {
          turn.degradedSources = (data.degraded_sources as string[]) ?? []
          break
        }
        case 'normalize': {
          turn.normalized = (data.normalized as NormalizedItem[]) ?? []
          break
        }
        case 'recommend': {
          turn.recommendation = (data.recommendation as Recommendation) ?? null
          const top = turn.recommendation?.items[0]
          if (!turn.impressed && top) {
            turn.impressed = true
            void recordEvent(session.id, 'impression', top?.sku_id)
          }
          break
        }
        case 'lock_price': {
          if (data.price_raised) turn.priceRaised = true
          break
        }
        default:
          break
      }
    },
    handleInterrupt(turn: Turn, data: Record<string, unknown>): void {
      if ((data as { type?: string }).type === 'confirm_order') {
        turn.draftOrder = (data as { draft_order?: DraftOrder }).draft_order ?? null
        turn.awaitingHitl = true
        turn.streaming = false
        // 只标 confirm 等待；lock_price/place_order 在 resume 后真正执行时才会进轨迹，
        // 避免「取消/涨价」分支未执行却残留 pending 节点导致不闭合
        pushTrace(turn, 'confirm', 'waiting', ' · 等待确认')
      }
    },
    handleDone(turn: Turn, data: Record<string, unknown>): void {
      turn.order = (data as OrderResult) ?? null
      turn.streaming = false
      turn.awaitingHitl = false
      // 下单完成/取消：用摘要替位候选卡（「已下单 xx · ¥xx」/「未下单」），与后端回写 messages 文案一致
      if (turn.order?.status === 'placed') {
        turn.agentReply = orderSummary(turn)
        turn.recommendation = null
        turn.draftOrder = null
      } else if (turn.order?.status === 'cancelled') {
        turn.agentReply = '未下单'
        turn.recommendation = null
        turn.draftOrder = null
      }
    },
    patchSubtitle(session: Session, prefs: Preferences | null): void {
      if (!prefs) return
      const parts: string[] = []
      if (prefs.category) parts.push(prefs.category)
      if (prefs.budget) parts.push(`预算 ¥${prefs.budget}`)
      if (parts.length) session.subtitle = parts.join(' · ')
    },
  },
})
