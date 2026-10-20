<script setup lang="ts">
import { computed, ref } from 'vue'
import { useChatStore } from '../stores/chat'
import type { Turn } from '../stores/chat'
import type { PriceInsight, RecommendItem } from '../api/types'
import OrderCard from './OrderCard.vue'

const props = defineProps<{ turn: Turn }>()
const store = useChatStore()

const rec = computed(() => props.turn.recommendation!)

// 平台展示样式（京东/淘宝/拼多多/主库兜底）
const PLATFORM: Record<string, { short: string; bg: string; name: string; logo?: string }> = {
  jd: { short: '京', bg: '#E1251B', name: '京东', logo: '/platforms/jd.png' },
  taobao: { short: '淘', bg: '#FF6F00', name: '淘宝', logo: '/platforms/tb.png' },
  pdd: { short: '拼', bg: '#E02E24', name: '拼多多', logo: '/platforms/pdd.png' },
  master: { short: '主', bg: '#44403C', name: '主库兜底' },
}

const PLATFORM_ALIASES: Record<string, string> = {
  '京东': 'jd',
  '淘宝': 'taobao',
  '天猫': 'taobao',
  '天猫精选': 'taobao',
  '淘宝精选': 'taobao',
  '拼多多': 'pdd',
}

function plat(p: string) {
  const name = p?.trim() ?? ''
  const key = PLATFORM_ALIASES[name] ?? name.toLowerCase()
  return PLATFORM[key] ?? { short: name.slice(0, 1).toUpperCase() || '店', bg: '#44403C', name: name || '未知平台' }
}
function platName(p: string) {
  return plat(p).name
}

function fmt(v: number | undefined | null): string {
  if (v == null) return '—'
  return `¥${Number.isInteger(v) ? v : v.toFixed(2)}`
}

// HITL 候选选择：用户点卡片后才允许确认下单
const selectedSkuId = ref('')
const selectedPlatform = ref('')
const selectedItem = computed(() => rec.value.items.find((i) => i.sku_id === selectedSkuId.value))
const bestItem = computed(() => rec.value.items.find((item) => item.sku_id === rec.value.best_sku_id))
const selectionDisabled = computed(() => ['placed', 'cancelled'].includes(props.turn.order?.status ?? ''))

function candidateLabel(item: RecommendItem): string {
  const index = rec.value.items.findIndex((candidate) => candidate.sku_id === item.sku_id)
  return `#${index + 1}`
}

function priceInsightText(insight: PriceInsight): string {
  const item = rec.value.items.find((candidate) => candidate.sku_id === insight.sku_id)
  const label = item ? candidateLabel(item) : insight.sku_id
  const current = fmt(insight.current_price)
  const reference = fmt(insight.reference_price)

  if (insight.assessment === 'wait') {
    return `${label}：当前价 ${current}，高于常卖价 ${reference}，当前价格不占优，可以等等再入手。`
  }
  return `${label}：当前价 ${current}，低于常卖价 ${reference}，当前价格较低，可以直接入手。`
}

function displayDecisionText(text: string): string {
  return rec.value.items.reduce(
    (display, item, index) => display.replaceAll(item.sku_id, `#${index + 1}`),
    text,
  )
}

function selectSku(item: RecommendItem): void {
  if (selectionDisabled.value) return
  const skuId = item.sku_id
  // 点卡片 = 选中候选：平台回落到该商品最优解 + 记录点击（同一轮同 SKU 只记一次）
  if (selectedSkuId.value === skuId) return
  selectedSkuId.value = skuId
  selectedPlatform.value = item.platform
  store.trackClick(skuId, props.turn.id)
}

function hasImage(item: RecommendItem): boolean {
  return Boolean(item.image_url)
}

interface HitlSummary {
  title: string
  platform: string
  final_price: number
  eta_days: number
  previous_final_price?: number
}
// 首轮使用所选候选的真实源价格；订单草稿由服务端事实构建。
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
  const it = selectedItem.value
  if (it) return { title: it.title, platform: it.platform, final_price: it.final_price, eta_days: 0 }
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
    </div>

    <!-- 候选商品卡片（点击埋点） -->
    <div
      v-for="item in rec.items"
      :key="item.sku_id"
      class="cand"
      :class="{ selected: selectedSkuId === item.sku_id && !selectionDisabled, 'selection-disabled': selectionDisabled }"
      @click="selectSku(item)"
    >
      <div class="thumb" :class="{ 'thumb-image': hasImage(item) }">
        <img v-if="hasImage(item)" :src="item.image_url" :alt="item.title" loading="lazy" />
        <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 12L2 9z" /><path d="M2 9h20M12 21 8 9l4-6 4 6-4 12" /></svg>
      </div>
      <div class="cand-content">
        <div class="cand-title-row">
          <h4 class="cand-title" :title="item.title">{{ item.title }}</h4>
          <!-- <span :class="isBestCandidate(item, idx) ? 'badge-best' : 'badge-alt'">{{ isBestCandidate(item, idx) ? '👍 推荐' : '备选' }}</span> -->
        </div>
        <div class="tags">
          <span :class="['tag', 'platform-tag', { 'platform-tag-logo': plat(item.platform).logo }]">
            <img
              v-if="plat(item.platform).logo"
              class="platform-mark platform-logo"
              :src="plat(item.platform).logo"
              :alt="platName(item.platform) + '标识'"
            />
            <span v-else class="platform-mark" :style="{ background: plat(item.platform).bg }" aria-hidden="true">{{ plat(item.platform).short }}</span>
            <template v-if="!plat(item.platform).logo">{{ platName(item.platform) }}</template>
          </span>
          <span v-for="(v, k) in item.attributes" :key="k" class="tag">{{ v }}</span>
        </div>
        <div class="price">
          {{ fmt(item.final_price) }}
        </div>
        <a
          v-if="item.url"
          class="product-link"
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          :title="item.url"
          @click.stop
        >
          查看详情>>>
        </a>
        <div v-else class="product-link product-link-empty">暂无商品链接</div>
      </div>
    </div>

    <!-- LLM 的候选横向比较结论，仅展示已验证的结构化字段。 -->
    <section class="decision-panel" aria-label="候选对比决策">
      <div class="decision-heading">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 12 2 2 4-4"/><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
        <h5>候选对比决策</h5>
      </div>
      <p v-if="rec.summary" class="decision-summary">{{ displayDecisionText(rec.summary) }}</p>
      <p v-if="bestItem" class="decision-best">建议优先选择：{{ candidateLabel(bestItem) }} {{ bestItem.title }}</p>

      <div v-if="rec.price_insights?.length" class="decision-price-insights">
        <p v-for="insight in rec.price_insights" :key="`${insight.sku_id}:${insight.reference_label}`" class="price-insight">
          {{ priceInsightText(insight) }}
        </p>
      </div>
    </section>

    <!-- HITL 下单确认 -->
    <div v-if="turn.draftOrder && turn.order?.status !== 'placed' && turn.order?.status !== 'cancelled'" class="hitl">
      <h3>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
        下单确认 · 人工确认
      </h3>
      <p>点击上方任一候选卡片选中商品，确认后生成模拟订单。</p>
      <div v-if="!turn.priceRaised && !selectedSkuId" class="select-hint">👆 请先点选一款候选商品，再确认下单</div>

      <div v-if="summary" class="order-summary">
        <div class="order-line"><span>{{ summary.title }} ×1</span><span>{{ fmt(summary.final_price) }}</span></div>
        <div class="order-line"><span>平台</span><span>{{ platName(summary.platform) }}</span></div>
        <!-- <div class="order-line"><span>快递时效</span><span>{{ summary.eta_days }} 天</span></div> -->
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
      <div v-if="turn.order?.status === 'placed'" class="lock-note show">下单成功 · 订单 #{{ turn.order.order_id }} 已锁定 {{ fmt(turn.order.final_price) }}</div>
      <div v-if="turn.order?.status !== 'placed'" class="btn-row">
        <button class="btn btn-primary" :disabled="turn.streaming || (!turn.priceRaised && !selectedSkuId)" @click="confirm">{{ turn.streaming ? '二次询价中…' : '确认下单' }}</button>
        <button class="btn btn-ghost" :disabled="turn.streaming" @click="cancel">取消</button>
      </div>
    </div>

    <OrderCard
      v-if="turn.order?.status === 'placed'"
      class="transaction-result"
      :order="turn.order"
    />
    <article v-else-if="turn.order?.status === 'cancelled'" class="order-card cancelled-card transaction-result" aria-label="模拟下单已取消">
      <div class="order-card-head">
        <div>
          <div class="order-card-kicker">模拟订单 · 已取消</div>
          <h3>本次未生成订单</h3>
        </div>
        <span class="order-status cancelled-status">已取消</span>
      </div>
      <div class="cancelled-card-body">
        <span class="cancelled-mark" aria-hidden="true">×</span>
        <p>推荐结果仍保留，你可以继续查看商品详情。</p>
      </div>
    </article>
  </div>
</template>
