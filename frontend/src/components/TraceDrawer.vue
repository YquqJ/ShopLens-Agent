<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()

const turn = computed(() => store.activeSession.turns.at(-1))
const shortThread = computed(() => store.activeSession.id.replace(/-/g, '').slice(0, 8))
</script>

<template>
  <aside class="trace" aria-label="Agent 执行轨迹">
    <div class="trace-head"><h2>Agent Trace</h2><span class="live"><span class="pulse"></span> Live</span></div>
    <div class="trace-sub">thread_id · conv_{{ shortThread }} · qwen-plus</div>

    <div class="tl">
      <div v-for="n in turn?.trace ?? []" :key="n.name" class="node" :class="n.status">
        <span class="dot"></span>
        <div class="nname">{{ n.name }}</div>
        <div class="nmeta">{{ n.label }}</div>
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
      <span>模型 <b>qwen-plus</b> · 路由 <b>qwen-turbo</b></span>
      <span v-if="turn?.degradedSources.length">已降级源 <b>{{ turn.degradedSources.join(' / ') }}</b></span>
      <span v-else>MCP 比价源 <b>3</b> · 优惠券/物流源 <b>2</b></span>
    </div>
  </aside>
</template>