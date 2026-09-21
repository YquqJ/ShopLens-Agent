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

const topNormalized = computed(() => {
  const top = rec.value.items[0]
  return top ? bySku.value[top.sku_id] : undefined
})

const degradedList = computed(() => props.turn.degradedSources)

// 方案 B：HITL 候选商品 / 平台选择（默认首项 + 其最优解）
const selectedSkuId = ref(rec.value.items[0]?.sku_id ?? '')
const selectedPlatform = ref(bySku.value[rec.value.items[0]?.sku_id ?? '']?.best?.platform ?? '')
const selectedItem = computed(() => rec.value.items.find((i) => i.sku_id === selectedSkuId.value) ?? rec.value.items[0])
const selectedQuotes = computed<Quote[]>(() => bySku.value[selectedSkuId.value]?.quotes ?? [])
const selectedBest = computed(() => bySku.value[selectedSkuId.value]?.best?.platform ?? '')
const selectedQuote = computed<Quote | null>(
  () => selectedQuotes.value.find((q) => q.platform === selectedPlatform.value) ?? selectedQuotes.value[0] ?? null,
)
function onSkuChange(): void {
  // 切换商品后，平台回落到该商品的最优解
  selectedPlatform.value = selectedBest.value
}

interface HitlSummary { title: string; platform: string; final_price: number; eta_days: number }
// 首轮用所选候选/平台的报价；涨价重确认用锁价后的 draft
const summary = computed<HitlSummary | null>(() => {
  if (props.turn.priceRaised && props.turn.draftOrder) {
    const d = props.turn.draftOrder
    return { title: d.title, platform: d.platform, final_price: d.final_price, eta_days: d.eta_days }
  }
  const q = selectedQuote.value
  const it = selectedItem.value
  if (q && it) return { title: it.title, platform: q.platform, final_price: q.final_price, eta_days: q.eta_days }
  return null
})

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
      :class="{ optimal: idx === 0 }"
      @click="store.trackClick(item.sku_id)"
    >
      <div class="thumb">
        <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 12L2 9z" /><path d="M2 9h20M12 21 8 9l4-6 4 6-4 12" /></svg>
      </div>
      <div>
        <h4>
          {{ item.title }}
          <span :class="idx === 0 ? 'badge-best' : 'badge-alt'">{{ idx === 0 ? '综合最优' : '备选' }}</span>
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
    <div v-if="topNormalized" style="padding:0 20px">
      <div class="cmp">
        <div class="cmp-heading">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
          {{ topNormalized.title }} 多平台比价（SKU 已归一）
        </div>
        <table>
          <thead><tr><th>平台</th><th>标价</th><th>优惠券</th><th>物流</th><th style="text-align:right">到手价</th></tr></thead>
          <tbody>
            <tr v-for="q in topNormalized.quotes" :key="q.platform" :class="{ best: q.platform === topNormalized.best.platform }">
              <td>
                <div class="plat">
                  <span class="logo" :style="{ background: plat(q.platform).bg }">{{ plat(q.platform).short }}</span>
                  {{ platName(q.platform) }}
                </div>
              </td>
              <td>{{ fmt(q.price) }}</td>
              <td class="diagonal">{{ q.discount ? `-¥${q.discount}` : '—' }}</td>
              <td>{{ q.eta_days }} 天{{ q.shipping_fee ? ` · 运费¥${q.shipping_fee}` : '' }}</td>
              <td style="text-align:right" class="final">
                {{ fmt(q.final_price) }}
                <span v-if="q.platform === topNormalized.best.platform" class="pill-opt">最优解</span>
              </td>
            </tr>
            <tr v-for="p in degradedList" :key="p" class="dead">
              <td>
                <div class="plat">
                  <span class="logo" :style="{ background: plat(p).bg }">{{ plat(p).short }}</span>
                  {{ platName(p) }}
                </div>
              </td>
              <td colspan="4" style="text-align:right">该平台暂不可比（熔断降级 · 冷却中）</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 最优候选溯源理由 -->
    <div v-if="rec.items[0]" class="reason" style="margin:0 20px">
      <h5>{{ rec.items[0].title }} 推荐理由 · 可溯源</h5>
      <div class="reason-item"><span class="src">理由</span>{{ rec.items[0].reason }}</div>
      <div v-if="topNormalized" class="reason-item">
        <span class="src">出处</span>
        {{ platName(topNormalized.best.platform) }} 标价 {{ fmt(topNormalized.best.price) }}，到手 {{ fmt(topNormalized.best.final_price) }}
      </div>
    </div>

    <!-- HITL 下单确认 -->
    <div v-if="turn.draftOrder" class="hitl">
      <h3>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
        下单确认 · 人工确认（HITL）
      </h3>
      <p>可切换候选商品与购买平台，确认后下单前会二次询价锁定价格。</p>

      <!-- 方案 B：候选商品 / 平台选择（仅首轮，涨价重确认时隐藏） -->
      <div v-if="!turn.priceRaised" style="margin:14px 20px 0;display:grid;gap:8px">
        <label style="display:grid;grid-template-columns:36px 1fr;align-items:center;gap:10px;font-size:13px;color:#A8A29E">
          <span>商品</span>
          <select v-model="selectedSkuId" @change="onSkuChange" style="background:#292524;color:#FAFAF9;border:1px solid #44403C;border-radius:8px;padding:8px 10px;font-size:13px">
            <option v-for="it in rec.items" :key="it.sku_id" :value="it.sku_id">{{ it.title }}</option>
          </select>
        </label>
        <label style="display:grid;grid-template-columns:36px 1fr;align-items:center;gap:10px;font-size:13px;color:#A8A29E">
          <span>平台</span>
          <select v-model="selectedPlatform" style="background:#292524;color:#FAFAF9;border:1px solid #44403C;border-radius:8px;padding:8px 10px;font-size:13px">
            <option v-for="q in selectedQuotes" :key="q.platform" :value="q.platform">
              {{ platName(q.platform) }} · 到手 {{ fmt(q.final_price) }}{{ q.platform === selectedBest ? '（最优）' : '' }}
            </option>
          </select>
        </label>
      </div>

      <div v-if="summary" class="order-summary">
        <div class="order-line"><span>{{ summary.title }} ×1</span><span>{{ fmt(summary.final_price) }}</span></div>
        <div class="order-line"><span>平台</span><span>{{ platName(summary.platform) }}</span></div>
        <div class="order-line"><span>快递时效</span><span>{{ summary.eta_days }} 天</span></div>
        <div class="order-line total"><span>应付合计</span><span>{{ fmt(summary.final_price) }}</span></div>
      </div>
      <div v-if="turn.priceRaised && turn.order?.status !== 'placed'" class="lock-note show">二次询价发现价格变动，请确认最新到手价后再下单。</div>
      <div v-if="turn.order?.status === 'placed'" class="lock-note show">下单成功 · 订单 #{{ turn.order.order_id }} 已锁定 {{ fmt(turn.order.final_price) }}</div>
      <div v-if="turn.order?.status !== 'placed'" class="btn-row">
        <button class="btn btn-primary" :disabled="turn.streaming" @click="confirm">{{ turn.streaming ? '二次询价中…' : '确认下单' }}</button>
        <button class="btn btn-ghost" :disabled="turn.streaming" @click="cancel">取消下单</button>
      </div>
    </div>
  </div>
</template>