<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import RecommendationCard from './RecommendationCard.vue'
import OrderCard from './OrderCard.vue'

const store = useChatStore()
const scrollEl = ref<HTMLElement>()

// 消息时间标签：今天只显时分，昨天显「昨天 HH:MM」，更早显「M.D」
function timeLabel(ts: number): string {
  const d = new Date(ts)
  const now = new Date()
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (diffDays === 0) return hm
  if (diffDays === 1) return `昨天 ${hm}`
  return `${d.getMonth() + 1}.${d.getDate()}`
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const container = scrollEl.value?.closest('.content') as HTMLElement | null
  if (container) container.scrollTop = container.scrollHeight
}

function clarificationRound(index: number): number {
  let count = 0
  const turns = store.activeSession.turns
  for (let cursor = index; cursor >= 0; cursor -= 1) {
    if (cursor !== index && (turns[cursor].contextReset || turns[cursor].order?.status === 'placed')) break
    if (turns[cursor].clarifyQuestion) count += 1
  }
  return count
}

// 深监听会话：流式推进（新 turn / 节点事件）时自动滚到底
watch(() => store.activeSession, scrollToBottom, { deep: true })
watch(() => store.activeSession.turns.length, scrollToBottom)
</script>

<template>
  <div ref="scrollEl" class="chat">
    <template v-for="(turn, i) in store.activeSession.turns" :key="turn.id">
      <div v-if="turn.ts" class="time-divider">{{ timeLabel(turn.ts) }}</div>
      <div class="msg user"><div class="bubble">{{ turn.user }}</div></div>

      <!-- 流式进行中：比价/推荐未出 -->
      <div v-if="turn.streaming && !turn.clarifyQuestion && !turn.recommendation && !turn.error" class="msg agent">
        <div class="avatar">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 1 4 4c0 .83-.26 1.6-.7 2.24a6 6 0 0 1 4.3 5.76v1a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1v-1a6 6 0 0 1 4.3-5.76A3.98 3.98 0 0 1 8 7a4 4 0 0 1 4-4z"/></svg>
        </div>
        <div class="bubble" style="border:none;background:transparent;box-shadow:none">
          <div class="status-msg"><span class="spinner"></span> 请稍等…</div>
        </div>
      </div>

      <!-- 澄清反问 -->
      <div v-if="turn.clarifyQuestion" class="msg agent">
        <div class="avatar">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 1 4 4c0 .83-.26 1.6-.7 2.24a6 6 0 0 1 4.3 5.76v1a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1v-1a6 6 0 0 1 4.3-5.76A3.98 3.98 0 0 1 8 7a4 4 0 0 1 4-4z"/></svg>
        </div>
        <div class="bubble">
          <div class="voice">ShopLens · 澄清（第 {{ clarificationRound(i) }} 轮）</div>
          {{ turn.clarifyQuestion }}
        </div>
      </div>

      <!-- 非导购兜底回复 -->
      <div v-if="turn.agentReply" class="msg agent">
        <div class="avatar">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 1 4 4c0 .83-.26 1.6-.7 2.24a6 6 0 0 1 4.3 5.76v1a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1v-1a6 6 0 0 1 4.3-5.76A3.98 3.98 0 0 1 8 7a4 4 0 0 1 4-4z"/></svg>
        </div>
        <div class="bubble">
          <div>{{ turn.agentReply }}</div>
          <a
            v-if="turn.agentReplyUrl"
            class="agent-link"
            :href="turn.agentReplyUrl"
            target="_blank"
            rel="noopener noreferrer"
          >链接：{{ turn.agentReplyUrl }}</a>
        </div>
      </div>

      <!-- 推荐结果 + 比价表 + HITL -->
      <RecommendationCard v-if="turn.recommendation?.items.length" :turn="turn" />
      <div v-else-if="turn.recommendation?.summary" class="msg agent">
        <div class="avatar">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 1 4 4c0 .83-.26 1.6-.7 2.24a6 6 0 0 1 4.3 5.76v1a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1v-1a6 6 0 0 1 4.3-5.76A3.98 3.98 0 0 1 8 7a4 4 0 0 1 4-4z"/></svg>
        </div>
        <div class="bubble">{{ turn.recommendation.summary }}</div>
      </div>

      <OrderCard v-if="turn.order?.status === 'placed'" :order="turn.order" />

      <!-- 异常 -->
      <div v-if="turn.error" class="msg agent">
        <div class="bubble" style="color:var(--destructive)">出错了：{{ turn.error }}</div>
      </div>
    </template>

    <div v-if="!store.activeSession.turns.length" class="empty-hint">
      输入购物需求开始导购，例如：我要买一条黑色牛仔裤，预算 200
    </div>
  </div>
</template>
