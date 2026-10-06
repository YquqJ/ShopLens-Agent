<script setup lang="ts">
import { computed, ref } from 'vue'
import { useChatStore } from '../stores/chat'
import type { SourceMode } from '../api/types'

const store = useChatStore()
const text = ref('')
const streaming = computed(() => store.activeSession.turns.at(-1)?.streaming === true)

async function send(): Promise<void> {
  const msg = text.value.trim()
  if (!msg) return
  text.value = ''
  await store.sendMessage(msg)
}

function chooseSourceMode(mode: SourceMode): void {
  store.setSourceMode(mode)
}
</script>

<template>
  <div class="source-mode" role="group" aria-label="数据源模式">
    <button
      type="button"
      class="source-mode-btn"
      :class="{ active: store.sourceMode === 'mock' }"
      :disabled="streaming"
      @click="chooseSourceMode('mock')"
    >仿真源</button>
    <button
      type="button"
      class="source-mode-btn"
      :class="{ active: store.sourceMode === 'real' }"
      :disabled="streaming || !store.sourceModes.real"
      :title="store.sourceModes.real ? '使用值得买真实商品源' : '后端未配置真实源 API Key'"
      @click="chooseSourceMode('real')"
    >真实源</button>
  </div>
  <textarea
    v-model="text"
    placeholder="描述你的购物需求，例如：我要买一条黑色牛仔裤，预算 200…"
    aria-label="输入购物需求"
    @keydown.enter.exact.prevent="send"
  ></textarea>
  <button class="send-btn" @click="send">发送</button>
</template>
