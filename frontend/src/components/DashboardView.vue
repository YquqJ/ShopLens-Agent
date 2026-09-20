<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { fetchFunnel } from '../api/client'
import type { FunnelStats } from '../api/types'

const stats = ref<FunnelStats | null>(null)
const loading = ref(false)
const funnelEl = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

function renderFunnel(): void {
  if (!funnelEl.value || !stats.value) return
  const s = stats.value
  if (!chart) chart = echarts.init(funnelEl.value)
  chart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [
      {
        type: 'funnel',
        left: '8%',
        right: '8%',
        top: 8,
        bottom: 8,
        minSize: '18%',
        sort: 'descending',
        gap: 2,
        label: { show: true, position: 'inside', formatter: '{b}\n{c}', color: '#fff', fontFamily: 'Montserrat' },
        itemStyle: { borderWidth: 0 },
        data: [
          { value: s.impression, name: '推荐曝光', itemStyle: { color: '#1C1917' } },
          { value: s.click, name: '点击查看', itemStyle: { color: '#57534E' } },
          { value: s.order, name: '完成下单', itemStyle: { color: '#A16207' } },
        ],
      },
    ],
  })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    stats.value = await fetchFunnel()
    await renderFunnel()
  } finally {
    loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => {
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="dash">
    <div class="kpi-grid">
      <div class="glass kpi"><div class="label">推荐曝光</div><div class="num">{{ stats?.impression ?? 0 }}</div><div class="delta">会话内累计</div></div>
      <div class="glass kpi"><div class="label">点击查看</div><div class="num">{{ stats?.click ?? 0 }}</div><div class="delta up">{{ stats ? pct(stats.click_rate) : '—' }} 点击率</div></div>
      <div class="glass kpi"><div class="label">完成下单</div><div class="num">{{ stats?.order ?? 0 }}</div><div class="delta up">{{ stats ? pct(stats.order_rate) : '—' }} 转化率</div></div>
      <div class="glass kpi"><div class="label">整体转化率</div><div class="num">{{ stats ? pct(stats.order_rate) : '—' }}</div><div class="delta">下单 / 曝光</div></div>
    </div>

    <div class="glass funnel">
      <h3>推荐 → 点击 → 下单 转化漏斗</h3>
      <div class="funnel-note">数据随会话实时累积 · 曝光/点击由前端埋点，下单由后端落库归因 <button class="btn btn-ghost" style="margin-left:12px;padding:5px 14px;font-size:12px" :disabled="loading" @click="load">刷新</button></div>
      <div v-if="stats && stats.impression === 0 && stats.click === 0 && stats.order === 0" class="empty-hint">暂无转化数据，完成一次「推荐 → 点击 → 下单」后即可在此看到漏斗</div>
      <div ref="funnelEl" v-show="stats && (stats.impression + stats.click + stats.order > 0)" style="height:340px"></div>
    </div>
  </div>
</template>