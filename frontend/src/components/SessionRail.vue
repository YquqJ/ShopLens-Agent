<script setup lang="ts">
import { useChatStore } from '../stores/chat'

const store = useChatStore()
</script>

<template>
  <aside class="rail">
    <button class="new-btn" @click="store.newSession()">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
      新建导购
    </button>
    <h4>会话</h4>
    <div class="sessions">
      <div v-for="s in store.sessions" :key="s.id" class="session-row">
        <button
          class="session"
          :class="{ active: s.id === store.activeId }"
          @click="store.selectSession(s.id)"
        >
          <div class="t">{{ s.title }}</div>
          <div class="s">{{ s.subtitle }}</div>
        </button>
        <button class="session-del" title="删除会话" @click.stop="store.deleteSession(s.id)">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </div>
  </aside>
</template>
