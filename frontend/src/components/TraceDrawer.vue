<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { fetchBreakers, setFailRate } from '../api/client'

const store = useChatStore()

const turn = computed(() => store.activeSession.turns.at(-1))
const streaming = computed(() => turn.value?.streaming === true)
const shortThread = computed(() => store.activeSession.id.replace(/-/g, '').slice(0, 8))
const sourceModeLabel = computed(() => store.sourceMode === 'real' ? '值得买真实源' : '本地仿真源')

const SOURCES = [
  { key: 'jd', label: '京东' },
  { key: 'taobao', label: '淘宝' },
  { key: 'pdd', label: '拼多多' },
]

const STATE_TEXT: Record<string, string> = { closed: '闭合', open: '打开', half_open: '半开' }

const failRates = ref<Record<string, number>>({ jd: 0, taobao: 0, pdd: 0 })
const breakerStates = ref<Record<string, string>>({})

let timer: number | undefined

async function refreshBreakers(): Promise<void> {
  try {
    breakerStates.value = await fetchBreakers()
  } catch {
    /* 调试面板静默失败，不影响主流程 */
  }
}

async function toggle(source: string): Promise<void> {
  const next = failRates.value[source] > 0 ? 0 : 1
  failRates.value[source] = next
  try {
    await setFailRate(source, next)
  } catch (e) {
    console.error('注入故障失败', e)
  }
}

function stateOf(key: string): string {
  return breakerStates.value[key] ?? 'closed'
}

function stateText(s: string): string {
  return STATE_TEXT[s] ?? s
}

function startPolling(): void {
  if (timer) return
  timer = window.setInterval(refreshBreakers, 1500)
}

function stopPolling(): void {
  if (timer) {
    window.clearInterval(timer)
    timer = undefined
  }
}

// 只在导购流式进行时轮询（此刻才可能触发熔断），空闲不请求；其余时候点「刷新状态」手动看
watch(streaming, (on) => {
  if (on) startPolling()
  else stopPolling()
})

onMounted(() => {
  void refreshBreakers()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <aside class="trace" aria-label="Agent 执行轨迹">
    <div class="trace-head"><h2>Agent Trace</h2><span class="live"><span class="pulse"></span> Live</span></div>
    <div class="trace-sub">thread_id · conv_{{ shortThread }} · qwen-plus</div>

    <div class="tl">
      <div v-for="n in turn?.trace ?? []" :key="n.name" class="node" :class="n.status">
        <span class="dot"></span>
        <div class="nname">{{ n.label }}</div>
        <div class="nmeta">{{ n.status === 'waiting' ? '等待用户操作' : '已完成' }}</div>
        <div class="ndesc">{{ n.desc }}</div>
      </div>
      <div v-if="!(turn?.trace?.length)" class="node pending">
        <span class="dot"></span>
        <div class="nname">idle</div>
        <div class="nmeta">等待发起导购</div>
        <div class="ndesc">发送一条购物需求开始跟踪节点</div>
      </div>
    </div>

    <div class="trace-footer">
      <span>数据源 <b>{{ sourceModeLabel }}</b></span>
      <span>模型 <b>qwen-plus</b></span>
      <span v-if="store.sourceMode === 'real'">实时商品检索 · <b>推荐模式</b></span>
      <template v-else>
        <span v-if="turn?.degradedSources.length">已降级源 <b>{{ turn.degradedSources.join(' / ') }}</b></span>
        <span v-else>MCP 比价源 <b>3</b> · 优惠券/物流源 <b>2</b></span>
      </template>
    </div>

    <div class="debug-panel">
      <div class="debug-head">
        <span class="debug-title">熔断演示 · 故障注入</span>
        <button class="debug-refresh" @click="refreshBreakers">刷新状态</button>
      </div>
      <div class="debug-row" v-for="s in SOURCES" :key="s.key">
        <span class="debug-src">{{ s.label }}</span>
        <button class="debug-toggle" :class="{ on: failRates[s.key] > 0 }" @click="toggle(s.key)">
          {{ failRates[s.key] > 0 ? '已注入' : '注入故障' }}
        </button>
        <span class="debug-state" :class="stateOf(s.key)">{{ stateText(stateOf(s.key)) }}</span>
      </div>
      <div class="debug-hint">注入后发一条导购，看「多源比价」节点：该源连续失败→熔断打开→降级，其余源照常出结果</div>
    </div>
  </aside>
</template>
