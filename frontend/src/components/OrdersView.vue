<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { fetchOrders } from '../api/client'
import type { OrderResult, OrdersPage } from '../api/types'

const props = defineProps<{ active: boolean }>()
const pageSize = 10
const page = ref(1)
const startDate = ref('')
const endDate = ref('')
const keyword = ref('')
const category = ref('')
const categories = ref<string[]>([])
const items = ref<OrderResult[]>([])
const total = ref(0)
const loading = ref(false)
const loaded = ref(false)
const error = ref('')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const visiblePages = computed(() => {
  const count = pageCount.value
  const start = Math.max(1, Math.min(page.value - 2, count - 4))
  return Array.from({ length: Math.min(5, count) }, (_, index) => start + index)
})

function money(value?: number | null): string {
  return value == null ? '—' : `¥${value.toFixed(2)}`
}

function time(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function platformLabel(value?: string | null): string {
  return ({ jd: '京东', taobao: '淘宝', pdd: '拼多多', master: '主库' } as Record<string, string>)[value ?? ''] ?? value ?? '—'
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const result: OrdersPage = await fetchOrders({
      page: page.value,
      pageSize,
      startDate: startDate.value,
      endDate: endDate.value,
      keyword: keyword.value,
      category: category.value,
    })
    items.value = result.items
    total.value = result.total
    categories.value = result.categories
    loaded.value = true
    if (page.value > pageCount.value) page.value = pageCount.value
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '订单列表加载失败'
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function applyFilters(): void {
  page.value = 1
  void load()
}

function resetFilters(): void {
  startDate.value = ''
  endDate.value = ''
  keyword.value = ''
  category.value = ''
  applyFilters()
}

function changePage(nextPage: number): void {
  if (loading.value || nextPage < 1 || nextPage > pageCount.value || nextPage === page.value) return
  page.value = nextPage
  void load()
}

onMounted(() => {
  if (props.active) void load()
})
watch(() => props.active, (active) => {
  if (active && !loaded.value) void load()
})
</script>

<template>
  <div class="orders-page">
    <div class="orders-page-head">
      <div>
        <div class="orders-kicker">ShopLens · ORDERS</div>
        <h1>订单</h1>
        <p>查看已完成的模拟订单，按时间、商品名称和品类快速筛选。</p>
      </div>
      <div class="orders-total"><strong>{{ total }}</strong><span>笔订单</span></div>
    </div>

    <form class="orders-filters" @submit.prevent="applyFilters">
      <label><span>开始日期</span><input v-model="startDate" type="date" aria-label="开始日期" /></label>
      <label><span>结束日期</span><input v-model="endDate" type="date" aria-label="结束日期" /></label>
      <label class="orders-search-field"><span>商品名称</span><input v-model="keyword" type="search" placeholder="搜索商品名称" aria-label="商品名称" /></label>
      <label><span>品类</span><select v-model="category" aria-label="品类"><option value="">全部品类</option><option v-for="item in categories" :key="item" :value="item">{{ item }}</option></select></label>
      <div class="orders-filter-actions"><button class="btn btn-primary" type="submit" :disabled="loading">查询</button><button class="btn btn-ghost" type="button" :disabled="loading" @click="resetFilters">重置</button></div>
    </form>

    <div class="orders-list-panel glass">
      <div class="orders-list-head"><h2>订单明细</h2><span v-if="total">第 {{ page }} / {{ pageCount }} 页</span></div>
      <div v-if="loading" class="orders-state" aria-live="polite"><span class="spinner"></span>正在加载订单…</div>
      <div v-else-if="error" class="orders-state orders-error" role="alert">{{ error }}</div>
      <div v-else-if="!items.length" class="orders-state">暂无符合条件的订单</div>
      <div v-else class="orders-table-wrap">
        <table class="orders-list-table">
          <thead><tr><th>商品</th><th>订单号</th><th>品类</th><th>平台</th><th>成交金额</th><th>下单时间</th></tr></thead>
          <tbody><tr v-for="order in items" :key="order.order_id ?? order.order_number">
            <td class="orders-product-cell"><div class="orders-product-thumb"><img v-if="order.image_url" :src="order.image_url" :alt="order.title || '商品图片'" /><span v-else>商品</span></div><strong :title="order.title">{{ order.title || '已购商品' }}</strong></td>
            <td>{{ order.order_number || `#${order.order_id ?? '—'}` }}</td><td>{{ order.category || '—' }}</td><td>{{ platformLabel(order.platform) }}</td><td class="orders-price">{{ money(order.final_price) }}</td><td class="orders-time">{{ time(order.created_at) }}</td>
          </tr></tbody>
        </table>
      </div>
      <div v-if="!loading && !error && pageCount > 1" class="orders-pagination" aria-label="订单分页">
        <button type="button" class="page-btn" :disabled="page === 1" aria-label="上一页" @click="changePage(page - 1)">‹</button>
        <button v-for="number in visiblePages" :key="number" type="button" class="page-btn" :class="{ active: number === page }" :aria-current="number === page ? 'page' : undefined" @click="changePage(number)">{{ number }}</button>
        <button type="button" class="page-btn" :disabled="page === pageCount" aria-label="下一页" @click="changePage(page + 1)">›</button>
      </div>
    </div>
  </div>
</template>
