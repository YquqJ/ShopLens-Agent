// SSE 事件负载类型 —— 对应 backend/app/api/chat.py 的 _sse 分帧 与 各节点返回结构

export interface Preferences {
  category: string | null
  budget: number | null
  style: string | null
  material: string | null
  scene: string | null
}

export interface Candidate {
  sku_id: string
  title: string
  category: string
  attributes: Record<string, string>
  price: number
  stock: number
  image_url: string
  score: number
}

export interface Coupon {
  amount: number
  threshold: number
}

export interface Quote {
  platform: string
  price: number
  coupon: Coupon | null
  discount: number
  shipping_fee: number
  eta_days: number
  final_price: number
  total_cost: number
}

export interface NormalizedItem {
  sku_id: string
  title: string
  category: string | null
  attributes: Record<string, string>
  quotes: Quote[]
  best: Quote
  final_price: number
}

export interface RecommendItem {
  sku_id: string
  title: string
  final_price: number
  platform: string
  attributes: Record<string, string>
  reason: string
}

export interface Recommendation {
  summary: string
  items: RecommendItem[]
  degraded_sources: string[]
}

export interface DraftOrder {
  sku_id: string
  title: string
  platform: string
  price: number
  discount: number
  shipping_fee: number
  eta_days: number
  final_price: number
}

export interface OrderResult {
  order_id?: number
  conversation_id?: number
  sku_id?: string
  platform?: string
  final_price?: number
  status?: string
}

export interface FunnelStats {
  impression: number
  click: number
  order: number
  click_rate: number
  order_rate: number
}

export interface SSEEvent {
  event: string
  data: Record<string, unknown>
}

// 会话历史（后端 /api/conversations* 读回 checkpointer 最新状态）
export interface ConversationSummary {
  thread_id: string
  title: string
  subtitle: string
  updated_at: string | null
}

export interface HistoryMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ConversationDetail {
  messages: HistoryMessage[]
  preferences: Preferences
  normalized: NormalizedItem[]
  recommendation: Recommendation | null
  draft_order: DraftOrder | null
  order: OrderResult | null
  price_raised: boolean
  awaiting_hitl: boolean
}