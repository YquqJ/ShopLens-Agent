<script setup lang="ts">
import { computed, ref } from 'vue'
import { useChatStore } from '../stores/chat'
import type { Turn } from '../stores/chat'
import type { NormalizedItem, Quote, RecommendItem } from '../api/types'

const props = defineProps<{ turn: Turn }>()
const store = useChatStore()

const rec = computed(() => props.turn.recommendation!)

// 平台展示样式（京东/淘宝/拼多多/主库兜底）
const PLATFORM: Record<string, { short: string; bg: string; name: string }> = {
  jd: { short: '京', bg: '#E1251B', name: '京东' },
  taobao: { short: '淘', bg: '#FF6F00', name: '淘宝' },
  pdd: { short: '拼', bg: '#E02E24', name: '拼多多' },
  master: { short: '主', bg: '#44403C', name: '主库兜底' },
}

function plat(p: string) {
  return PLATFORM[p] ?? { short: p.slice(0, 1).toUpperCase(), bg: '#44403C', name: p }
}
function platName(p: string) {
  return plat(p).name
}

function fmt(v: number | undefined | null): string {
  if (v == null) return '—'
  return `¥${Number.isInteger(v) ? v : v.toFixed(2)}`
}

// 归一结果按 sku_id 索引，取 best 报价 + 三源 quotes（比价表 / 原价划线用）
const bySku = computed<Record<string, NormalizedItem>>(() => {
  const m: Record<string, NormalizedItem> = {}
  for (const n of props.turn.normalized) m[n.sku_id] = n
  return m
})

const degradedList = computed(() => props.turn.degradedSources)

// HITL 候选选择：默认不选中——最推荐那条只打「推荐」标记，用户点卡片才算「点击查看」并选中
const selectedSkuId = ref('')
const selectedPlatform = ref('')
const selectedItem = computed(() => rec.value.items.find((i) => i.sku_id === selectedSkuId.value))
// 比价表/理由区展示对象：点选哪个候选就跟到哪个，未选中默认回落到推荐品 item[0]
const activeItem = computed(() => selectedItem.value ?? rec.value.items[0])
const activeNormalized = computed(() => (activeItem.value ? bySku.value[activeItem.value.sku_id] : undefined))
const selectedQuotes = computed<Quote[]>(() => bySku.value[selectedSkuId.value]?.quotes ?? [])
const selectedBest = computed(() => bySku.value[selectedSkuId.value]?.best?.platform ?? '')
const selectedQuote = computed<Quote | null>(
  () => selectedQuotes.value.find((q) => q.platform === selectedPlatform.value) ?? selectedQuotes.value[0] ?? null,
)
function selectSku(skuId: string): void {
  // 点卡片 = 选中候选：平台回落到该商品最优解 + 记录点击（同一轮同 SKU 只记一次）
  if (selectedSkuId.value === skuId) return
  selectedSkuId.value = skuId
  selectedPlatform.value = selectedBest.value
  store.trackClick(skuId, props.turn.id)
}

interface HitlSummary {
  title: string
  platform: string
  final_price: number
  eta_days: number
  previous_final_price?: number
}
// 首轮用所选候选/平台的报价；涨价重确认用锁价后的 draft
const summary = computed<HitlSummary | null>(() => {
  if (props.turn.priceRaised && props.turn.draftOrder) {
    const d = props.turn.draftOrder
    return {
      title: d.title,
      platform: d.platform,
      final_price: d.final_price,
      eta_days: d.eta_days,
      previous_final_price: d.previous_final_price,
    }
  }
  const q = selectedQuote.value
  const it = selectedItem.value
  if (q && it) return { title: it.title, platform: q.platform, final_price: q.final_price, eta_days: q.eta_days }
  return null
})

const priceChange = computed(() => {
  const previous = summary.value?.previous_final_price
  const current = summary.value?.final_price
  if (previous == null || current == null || previous === current) return null
  const delta = roundCurrency(current - previous)
  return { direction: delta > 0 ? 'up' : 'down', amount: Math.abs(delta) }
})

function roundCurrency(value: number): number {
  return Math.round(value * 100) / 100
}

function origPrice(item: RecommendItem): number | null {
  const b = bySku.value[item.sku_id]?.best
  if (!b || b.price === b.final_price) return null
  return b.price
}

async function confirm(): Promise<void> {
  await store.resumeHitl(props.turn.id, true, selectedSkuId.value, selectedPlatform.value)
}
async function cancel(): Promise<void> {
  await store.resumeHitl(props.turn.id, false)
}
</script>

<template>
  <div class="glass" style="animation:rise .3s ease both">
    <div style="padding:20px 20px 0">
      <div class="cand-head">为你找到 {{ rec.items.length }} 款候选</div>
      <p v-if="rec.summary" class="why" style="margin-top:8px">{{ rec.summary }}</p>
    </div>

    <!-- 候选商品卡片（点击埋点） -->
    <div
      v-for="(item, idx) in rec.items"
      :key="item.sku_id"
      class="cand"
      :class="{ selected: selectedSkuId === item.sku_id }"
      @click="selectSku(item.sku_id)"
    >
      <div class="thumb">
        <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 12L2 9z" /><path d="M2 9h20M12 21 8 9l4-6 4 6-4 12" /></svg>
      </div>
      <div>
        <h4>
          {{ item.title }}
          <span :class="idx === 0 ? 'badge-best' : 'badge-alt'">{{ idx === 0 ? '👍 推荐' : '备选' }}</span>
        </h4>
        <div class="tags">
          <span v-for="(v, k) in item.attributes" :key="k" class="tag">{{ v }}</span>
        </div>
        <div class="price">
          {{ fmt(item.final_price) }}
          <span v-if="origPrice(item) != null" class="orig">{{ fmt(origPrice(item)) }}</span>
        </div>
        <div class="why">{{ item.reason }}</div>
      </div>
    </div>

    <!-- 最优候选多平台比价表 -->
    <div v-if="activeNormalized && !!selectedSkuId" style="padding:0 20px">
      <div class="cmp">
        <div class="cmp-heading">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
          {{ activeNormalized.title }} 多平台比价（SKU 已归一）
        </div>
        <table>
          <thead><tr><th>平台</th><th>标价</th><th>优惠券</th><th>物流（1天折算¥10）</th><th style="text-align:right">到手价</th><th style="text-align:right">综合成本</th></tr></thead>
          <tbody>
            <tr v-for="q in activeNormalized.quotes" :key="q.platform" :class="{ best: q.platform === activeNormalized.best.platform }">
              <td>
                <div class="plat">
                  <span class="logo" :style="{ background: plat(q.platform).bg }">{{ plat(q.platform).short }}</span>
                  {{ platName(q.platform) }}
                </div>
              </td>
              <td>{{ fmt(q.price) }}</td>
              <td class="diagonal">{{ q.discount ? `-¥${q.discount}` : '—' }}</td>
              <td>{{ q.eta_days }} 天{{ q.shipping_fee ? ` · 运费¥${q.shipping_fee}` : '' }}</td>
              <td style="text-align:right">
                {{ fmt(q.final_price) }}
              </td>
              <td style="text-align:right" class="final">
                {{ fmt(q.total_cost) }}
                <span v-if="q.platform === activeNormalized.best.platform" class="pill-opt">最优解</span>
              </td>
            </tr>
            <tr v-for="p in degradedList" :key="p" class="dead">
              <td>
                <div class="plat">
                  <span class="logo" :style="{ background: plat(p).bg }">{{ plat(p).short }}</span>
                  {{ platName(p) }}
                </div>
              </td>
              <td colspan="5" style="text-align:right">该平台暂不可比（熔断降级 · 冷却中）</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 最优候选溯源理由 -->
    <div v-if="activeItem && !!selectedSkuId" class="reason" style="margin:0 20px">
      <h5>{{ activeItem.title }} 推荐理由 · 可溯源</h5>
      <div class="reason-item"><span class="src">理由</span>{{ activeItem.reason }}</div>
      <div v-if="activeNormalized" class="reason-item">
        <span class="src">出处</span>
        {{ platName(activeNormalized.best.platform) }} 标价 {{ fmt(activeNormalized.best.price) }}，到手 {{ fmt(activeNormalized.best.final_price) }}，综合成本 {{ fmt(activeNormalized.best.total_cost) }}
      </div>
    </div>

    <!-- HITL 下单确认 -->
    <div v-if="turn.draftOrder" class="hitl">
      <h3>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
        下单确认 · 人工确认（HITL）
      </h3>
      <p>点击上方任一候选卡片选中商品，下单前会二次询价锁定价格。</p>
      <div v-if="!turn.priceRaised && !selectedSkuId" class="select-hint">👆 请先点选一款候选商品，再确认下单</div>

      <div v-if="summary" class="order-summary">
        <div class="order-line"><span>{{ summary.title }} ×1</span><span>{{ fmt(summary.final_price) }}</span></div>
        <div class="order-line"><span>平台</span><span>{{ platName(summary.platform) }}</span></div>
        <div class="order-line"><span>快递时效</span><span>{{ summary.eta_days }} 天</span></div>
        <div class="order-line total">
          <span>应付合计</span>
          <span>
            {{ fmt(summary.final_price) }}
            <span v-if="priceChange" class="price-change" :class="priceChange.direction">
              {{ priceChange.direction === 'up' ? '↑' : '↓' }} {{ fmt(priceChange.amount) }}
            </span>
          </span>
        </div>
      </div>
      <div v-if="turn.priceRaised && turn.order?.status !== 'placed'" class="lock-note show">二次询价发现价格变动，请确认最新到手价后再下单。</div>
      <div v-if="turn.order?.status === 'placed'" class="lock-note show">下单成功 · 订单 #{{ turn.order.order_id }} 已锁定 {{ fmt(turn.order.final_price) }}</div>
      <div v-if="turn.order?.status !== 'placed'" class="btn-row">
        <button class="btn btn-primary" :disabled="turn.streaming || (!turn.priceRaised && !selectedSkuId)" @click="confirm">{{ turn.streaming ? '二次询价中…' : '确认下单' }}</button>
        <button class="btn btn-ghost" :disabled="turn.streaming" @click="cancel">取消下单</button>
      </div>
    </div>
  </div>
</template>
