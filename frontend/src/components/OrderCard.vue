<script setup lang="ts">
import type { OrderResult } from '../api/types'

const props = defineProps<{ order: OrderResult }>()

function formatTime(value?: string | null): string {
  if (!value) return '刚刚'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function formatPrice(value?: number): string {
  if (value == null) return '—'
  return `¥${Number.isInteger(value) ? value : value.toFixed(2)}`
}
</script>

<template>
  <article class="order-card" aria-label="模拟订单">
    <div class="order-card-head">
      <div>
        <div class="order-card-kicker">模拟订单 · 已下单</div>
        <h3>订单号： {{ props.order.order_number || `#${props.order.order_id ?? '—'}` }}</h3>
      </div>
      <span class="order-status">已确认</span>
    </div>
    <div class="order-card-body">
      <div class="order-thumb" :class="{ placeholder: !props.order.image_url }">
        <img v-if="props.order.image_url" :src="props.order.image_url" :alt="props.order.title || '商品图片'" />
        <span v-else>商品</span>
      </div>
      <div class="order-info">
        <h4>{{ props.order.title || '已选商品' }}</h4>
        <div class="order-meta">下单时间 {{ formatTime(props.order.created_at) }}</div>
        <div class="order-meta">订单号 {{ props.order.order_number || `#${props.order.order_id ?? '—'}` }}</div>
      </div>
      <div class="order-price">{{ formatPrice(props.order.final_price) }}</div>
    </div>
  </article>
</template>
