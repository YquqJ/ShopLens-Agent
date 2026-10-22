// Pinia 会话 store：会话列表 + 每轮导购的 SSE 事件流状态机。
//
// 一轮导购 = 用户一句话 → POST /chat 返回整条 SSE 流（clarify→…→recommend→interrupt），
// 前端把 node/interrupt/done/error 四类事件逐步落到当前 Turn 上，驱动消息气泡 + 推荐卡 + HITL + Trace 抽屉。
import { reactive } from 'vue'
import { defineStore } from 'pinia'
import {
  chatStream,
  deleteConversation,
  fetchConversation,
  fetchConversations,
  resumeStream,
} from '../api/client'
import type {
  ConversationDetail,
  DraftOrder,
  OrderResult,
  OrderQuery,
  OrderSummary,
  OrderQueryHistoryEntry,
  Preferences,
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
  agentReplyUrl: string
  preferences: Preferences | null
  recommendation: Recommendation | null
  recommendationIntro: string
  degradedSources: string[]
  draftOrder: DraftOrder | null
  order: OrderResult | null
  orderQuery: OrderQuery | null
  orderResults: OrderResult[]
  orderSummary: OrderSummary | null
  orderClarification: string
  orderError: string
  trace: TraceNode[]
  streaming: boolean
  awaitingHitl: boolean
  priceRaised: boolean
  error: string
  contextReset: boolean
}

export interface Session {
  id: string
  title: string
  subtitle: string
  turns: Turn[]
}

// 节点名 → Trace 抽屉展示文案（对齐原型右侧 Agent Trace）
const NODE_META: Record<string, { label: string; desc: string }> = {
  clarify: { label: '澄清', desc: '提取搜索条件' },
  route: { label: '意图路由', desc: '判断购物 / 订单 / 其他' },
  parse_order_query: { label: '解析订单查询', desc: '解析时间范围与筛选条件' },
  query_orders: { label: '检索订单', desc: '查询已落库的订单快照' },
  summarize_orders: { label: '消费统计', desc: '计算订单数、总金额与平均客单价' },
  retrieve: { label: '商品检索', desc: '搜索候选商品' },
  recommend: { label: '可解释推荐', desc: '生成可溯源推荐理由' },
  confirm: { label: '下单确认', desc: 'HITL：人工确认下单' },
  place_order: { label: '下单落库', desc: '订单写入 PostgreSQL' },
}

function formatPreferences(prefs: Preferences | null): string {
  if (!prefs) return ''
  const parts: string[] = []
  if (prefs.category) parts.push(prefs.category)
  if (prefs.brand) parts.push(prefs.brand)
  if (prefs.gender) parts.push(prefs.gender)
  if (prefs.color) parts.push(prefs.color)
  if (prefs.use_case) parts.push(prefs.use_case)
  if (prefs.budget != null) parts.push(`预算 ¥${prefs.budget}`)
  if (prefs.requirements) parts.push(prefs.requirements)
  return parts.join(' · ')
}

function newTurn(user: string, ts?: number, id?: string): Turn {
  // 必须 reactive：sendMessage 手里攥着原始引用，后续 SSE 事件直接改它的字段，
  // 若为普通对象会绕过 Vue Proxy 的 set 陷阱 → 数据更新但组件不重渲染（spinner 卡死）。
  return reactive({
    id: id ?? crypto.randomUUID(),
    user,
    ts: ts ?? Date.now(),
    clarifyQuestion: '',
    agentReply: '',
    agentReplyUrl: '',
    preferences: null,
    recommendation: null,
    recommendationIntro: '',
    degradedSources: [],
    draftOrder: null,
    order: null,
    orderQuery: null,
    orderResults: [],
    orderSummary: null,
    orderClarification: '',
    orderError: '',
    trace: [],
    streaming: true,
    awaitingHitl: false,
    priceRaised: false,
    error: '',
    contextReset: false,
  })
}

function hydrateTurns(d: ConversationDetail): Turn[] {
  // messages 按「user 开新轮」切分，结构化卡片随后按 turn_id 逐轮挂回。
  const turns: Turn[] = []
  const turnsById = new Map<string, Turn>()
  let cur: Turn | null = null
  for (const m of d.messages) {
    if (m.role === 'user') {
      cur = newTurn(m.content, m.ts != null ? m.ts * 1000 : undefined, m.turn_id)
      cur.streaming = false
      turns.push(cur)
      turnsById.set(cur.id, cur)
    } else if (cur) {
      const q = m.content
      if (q === '请补充明确的时间范围，例如“昨天”“上周”或“最近 30 天”。') {
        cur.orderClarification = q
        continue
      }
      if (q.endsWith('？') || q.endsWith('?')) cur.clarifyQuestion = q
      else if (q.startsWith('已选择「')) {
        const match = q.match(/^已选择「(.+?)」\n链接：(https?:\/\/\S+)$/)
        if (match) {
          cur.agentReply = `已选择「${match[1]}」`
          cur.agentReplyUrl = match[2]
        } else {
          cur.agentReply = q
        }
        cur.contextReset = true
      } else if (q === '未选择推荐商品' || q === REAL_CANCEL_REPLY) {
        cur.agentReply = q
      } else cur.agentReply = q
    }
  }
  const last = turns.at(-1)
  if (last) {
    last.preferences = d.preferences && Object.keys(d.preferences).length ? d.preferences : null
    const resolved = d.order?.status === 'placed' || d.order?.status === 'cancelled'
    last.draftOrder = resolved ? null : d.draft_order
    last.priceRaised = d.price_raised
    last.awaitingHitl = resolved ? false : d.awaiting_hitl
    last.orderQuery = d.order_query ?? null
    last.orderResults = d.order_results ?? []
    last.orderSummary = d.order_summary ?? null
    last.orderClarification = d.order_clarification ?? ''
    last.orderError = d.order_error ?? ''
  }

  for (const entry of d.recommendation_history ?? []) {
    const turn = turnsById.get(entry.turn_id)
    if (!turn || !entry.recommendation) continue
    turn.recommendation = entry.recommendation
    turn.recommendationIntro = entry.intro ?? ''
    turn.degradedSources = entry.recommendation.degraded_sources ?? []
  }
  for (const entry of d.order_history ?? []) {
    const turn = turnsById.get(entry.turn_id)
    if (!turn || !entry.order) continue
    turn.order = entry.order
  }

  const orderQueries = new Map<string, OrderQueryHistoryEntry>()
  for (const entry of d.order_query_history ?? []) {
    orderQueries.set(entry.turn_id, entry)
  }
  for (const [turnId, entry] of orderQueries) {
    const turn = turnsById.get(turnId)
    if (!turn) continue
    turn.orderQuery = entry.query
    turn.orderResults = entry.results ?? []
    turn.orderSummary = entry.summary ?? null
    turn.orderClarification = entry.clarification ?? ''
    turn.orderError = entry.error ?? ''
  }

  // 兼容旧 checkpoint：旧数据没有逐轮 history，只能把最新结果挂到最后一轮。
  if (last && !(d.recommendation_history?.length)) {
    last.recommendation = d.recommendation?.summary || d.recommendation?.items?.length ? d.recommendation : null
    last.recommendationIntro = d.recommendation_intro ?? ''
    last.degradedSources = d.recommendation?.degraded_sources ?? []
  }
  if (last && !(d.order_history?.length)) {
    last.order = d.order
  }
  return turns
}

function pushTrace(turn: Turn, name: string, status: TraceNode['status'], extra?: string): void {
  // 复用同名节点：interrupt 后 resume 会再次更新下单节点状态。
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

const REAL_CANCEL_REPLY = '好的，已取消本次商品选择。你可以继续说明购物需求，也可以说“退出导购”结束本轮。'

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
        this.loadedIds.push(id)
      } catch (e) {
        console.error('会话详情加载失败', e)
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
      await this.run(turn, () => chatStream(
        session.id,
        text,
        (ev) => this.handleEvent(session, turn, ev),
      ))
    },
    async resumeHitl(turnId: string, confirm: boolean, skuId?: string, platform?: string): Promise<void> {
      const session = this.activeSession
      const turn = session.turns.find((t) => t.id === turnId)
      if (!turn) return
      turn.awaitingHitl = false
      turn.streaming = true
      await this.run(turn, () => resumeStream(session.id, confirm, (ev) => this.handleEvent(session, turn, ev), skuId, platform))
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
      } else if (ev.event === 'recommendation_preview') {
        this.handleRecommendationPreview(turn, ev.data)
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
          const tracePreferences = (data.trace_preferences as Preferences | undefined) ?? turn.preferences
          const preferenceText = formatPreferences(tracePreferences)
          if (preferenceText) pushTrace(turn, 'clarify', 'done', ` · 本轮偏好：${preferenceText}`)
          this.patchSubtitle(session, turn.preferences)
          break
        }
        case 'route': {
          turn.agentReply = String(data.reply ?? '')
          break
        }
        case 'parse_order_query': {
          turn.orderQuery = (data.order_query as OrderQuery | null) ?? null
          turn.orderClarification = String(data.order_clarification ?? '')
          break
        }
        case 'query_orders': {
          turn.orderResults = (data.order_results as OrderResult[]) ?? []
          turn.orderError = String(data.order_error ?? '')
          break
        }
        case 'summarize_orders': {
          turn.orderSummary = (data.order_summary as OrderSummary | null) ?? null
          break
        }
        case 'retrieve': {
          const candidates = (data.candidates as unknown[]) ?? []
          const detail = ` · MCP 真实源召回 ${candidates.length} 条商品`
          pushTrace(turn, 'retrieve', 'done', detail)
          break
        }
        case 'recommend': {
          turn.recommendation = (data.recommendation as Recommendation) ?? null
          turn.recommendationIntro = String(data.recommendation_intro ?? '')
          break
        }
        default:
          break
      }
    },
    handleRecommendationPreview(turn: Turn, data: Record<string, unknown>): void {
      turn.recommendation = (data.recommendation as Recommendation) ?? null
      turn.recommendationIntro = String(data.recommendation_intro ?? '')
    },
    handleInterrupt(turn: Turn, data: Record<string, unknown>): void {
      if ((data as { type?: string }).type === 'confirm_order') {
        turn.draftOrder = (data as { draft_order?: DraftOrder }).draft_order ?? null
        turn.awaitingHitl = true
        turn.streaming = false
        // 只标 confirm 等待；place_order 在 resume 后真正执行时才会进轨迹，
        // 避免「取消/涨价」分支未执行却残留 pending 节点导致不闭合
        pushTrace(turn, 'confirm', 'waiting', ' · 等待确认')
      }
    },
    handleDone(turn: Turn, data: Record<string, unknown>): void {
      turn.order = (data as OrderResult) ?? null
      turn.streaming = false
      turn.awaitingHitl = false
      // 下单完成/取消：保留推荐卡；成交结果由订单卡承载，不额外插入 AI 气泡。
      if (turn.order?.status === 'placed') {
        turn.draftOrder = null
        turn.contextReset = true
      } else if (turn.order?.status === 'cancelled') {
        turn.draftOrder = null
        turn.contextReset = true
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
