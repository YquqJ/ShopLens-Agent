// Pinia 会话 store：会话列表 + 每轮导购的 SSE 事件流状态机。
//
// 一轮导购 = 用户一句话 → POST /chat 返回整条 SSE 流（clarify→…→recommend→interrupt），
// 前端把 node/interrupt/done/error 四类事件逐步落到当前 Turn 上，驱动消息气泡 + 比价表 + HITL + Trace 抽屉。
import { reactive } from 'vue'
import { defineStore } from 'pinia'
import { chatStream, recordEvent, resumeStream } from '../api/client'
import type {
  DraftOrder,
  NormalizedItem,
  OrderResult,
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
  lock_price: { label: '二次询价锁价', desc: '下单前重新报价锁定价格' },
  place_order: { label: '下单落库', desc: '订单写入 PostgreSQL' },
}

function newTurn(user: string): Turn {
  // 必须 reactive：sendMessage 手里攥着原始引用，后续 SSE 事件直接改它的字段，
  // 若为普通对象会绕过 Vue Proxy 的 set 陷阱 → 数据更新但组件不重渲染（spinner 卡死）。
  return reactive({
    id: crypto.randomUUID(),
    user,
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
    priceRaised: false,
    error: '',
  })
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

export const useChatStore = defineStore('chat', {
  state: () => {
    const first: Session = {
      id: crypto.randomUUID(),
      title: '新对话',
      subtitle: '等待输入购物需求',
      turns: [],
    }
    return { sessions: [first], activeId: first.id }
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
      if (this.sessions.some((s) => s.id === id)) this.activeId = id
    },
    async sendMessage(text: string): Promise<void> {
      const session = this.activeSession
      const turn = newTurn(text)
      session.turns.push(turn)
      if (session.turns.length === 1) session.title = text.slice(0, 18)
      await this.run(turn, () => chatStream(session.id, text, (ev) => this.handleEvent(session, turn, ev)))
    },
    async resumeHitl(turnId: string, confirm: boolean): Promise<void> {
      const session = this.activeSession
      const turn = session.turns.find((t) => t.id === turnId)
      if (!turn) return
      turn.awaitingHitl = false
      turn.streaming = true
      await this.run(turn, () => resumeStream(session.id, confirm, (ev) => this.handleEvent(session, turn, ev)))
    },
    trackClick(skuId: string): void {
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
          if (!turn.impressed) {
            turn.impressed = true
            const top = turn.recommendation?.items[0]
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
        pushTrace(turn, 'confirm', 'waiting', ' · HITL：等待确认下单')
        pushTrace(turn, 'lock_price', 'pending')
        pushTrace(turn, 'place_order', 'pending')
      }
    },
    handleDone(turn: Turn, data: Record<string, unknown>): void {
      turn.order = (data as OrderResult) ?? null
      turn.streaming = false
      turn.awaitingHitl = false
      // 下单完成（place_order 后 done 携带已落库订单）
      if (turn.draftOrder && turn.order?.status === 'cancelled') {
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