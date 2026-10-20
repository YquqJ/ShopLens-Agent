<script setup lang="ts">
import type { OrderResult, OrderSummary } from '../api/types'

defineProps<{
  results: OrderResult[]
  summary: OrderSummary | null
  queryLabel?: string
}>()

function money(value?: number | null): string {
  return value == null ? '—' : `¥${value.toFixed(2)}`
}

function time(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="order-query-results">
    <div v-if="summary" class="order-summary">
      <div><span>订单数</span><strong>{{ summary.order_count }}</strong></div>
      <div><span>总金额</span><strong>{{ money(summary.total_amount) }}</strong></div>
      <div><span>平均客单价</span><strong>{{ money(summary.average_order_amount) }}</strong></div>
      <div v-for="group in summary.groups" :key="group.key" class="order-summary-group">
        <span>{{ group.key }}</span><strong>{{ group.order_count }} 单 · {{ money(group.total_amount) }}</strong>
      </div>
    </div>
    <div v-if="results.length" class="order-query-list">
      <article v-for="order in results" :key="order.order_id ?? order.order_number" class="order-query-item">
        <div class="order-query-thumb" :class="{ placeholder: !order.image_url }">
          <img v-if="order.image_url" :src="order.image_url" :alt="order.title || '商品图片'" />
          <span v-else>商品</span>
        </div>
        <div class="order-query-info">
          <strong>{{ order.title || '已购商品' }}</strong>
          <span>{{ order.order_number || `#${order.order_id ?? '—'}` }} · {{ time(order.created_at) }}</span>
          <span>{{ order.platform || '—' }}</span>
        </div>
        <strong class="order-query-price">{{ money(order.final_price) }}</strong>
      </article>
    </div>
    <div v-else class="order-query-empty">{{ summary ? '该时间范围内没有订单。' : '没有找到符合条件的订单。' }}</div>
  </div>
</template>
