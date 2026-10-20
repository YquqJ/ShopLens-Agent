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
      <span>数据源 <b>值得买真实 MCP</b></span>
      <span>模型 <b>qwen-plus</b></span>
      <span>知识图谱增强 · <b>事实约束推荐</b></span>
      <span v-if="turn?.degradedSources.length">已降级源 <b>{{ turn.degradedSources.join(' / ') }}</b></span>
    </div>
  </aside>
</template>
