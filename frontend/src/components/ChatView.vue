<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import RecommendationCard from './RecommendationCard.vue'

const store = useChatStore()
const scrollEl = ref<HTMLElement>()

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

// 深监听会话：流式推进（新 turn / 节点事件）时自动滚到底
watch(() => store.activeSession, scrollToBottom, { deep: true })
</script>

<template>
  <div ref="scrollEl" class="chat">
    <template v-for="(turn, i) in store.activeSession.turns" :key="turn.id">
      <div class="msg user"><div class="bubble">{{ turn.user }}</div></div>

      <!-- 流式进行中：比价/推荐未出 -->
      <div v-if="turn.streaming && !turn.clarifyQuestion && !turn.recommendation && !turn.error" class="msg agent">
        <div class="avatar">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 1 4 4c0 .83-.26 1.6-.7 2.24a6 6 0 0 1 4.3 5.76v1a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1v-1a6 6 0 0 1 4.3-5.76A3.98 3.98 0 0 1 8 7a4 4 0 0 1 4-4z"/></svg>
        </div>
        <div class="bubble" style="border:none;background:transparent;box-shadow:none">
          <div class="status-msg"><span class="spinner"></span> 正在同步多平台实时报价…</div>
        </div>
      </div>

      <!-- 澄清反问 -->
      <div v-if="turn.clarifyQuestion" class="msg agent">
        <div class="avatar">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 1 4 4c0 .83-.26 1.6-.7 2.24a6 6 0 0 1 4.3 5.76v1a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1v-1a6 6 0 0 1 4.3-5.76A3.98 3.98 0 0 1 8 7a4 4 0 0 1 4-4z"/></svg>
        </div>
        <div class="bubble">
          <div class="voice">ShopLens · 澄清（第 {{ i + 1 }} 轮）</div>
          {{ turn.clarifyQuestion }}
        </div>
      </div>

      <!-- 非导购兜底回复 -->
      <div v-if="turn.agentReply" class="msg agent">
        <div class="avatar">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 1 4 4c0 .83-.26 1.6-.7 2.24a6 6 0 0 1 4.3 5.76v1a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1v-1a6 6 0 0 1 4.3-5.76A3.98 3.98 0 0 1 8 7a4 4 0 0 1 4-4z"/></svg>
        </div>
        <div class="bubble">{{ turn.agentReply }}</div>
      </div>

      <!-- 推荐结果 + 比价表 + HITL -->
      <RecommendationCard v-if="turn.recommendation" :turn="turn" />

      <!-- 异常 -->
      <div v-if="turn.error" class="msg agent">
        <div class="bubble" style="color:var(--destructive)">出错了：{{ turn.error }}</div>
      </div>
    </template>

    <div v-if="!store.activeSession.turns.length" class="empty-hint">
      输入购物需求开始导购，例如：给女朋友买条简约的项链，预算 2000
    </div>
  </div>
</template>