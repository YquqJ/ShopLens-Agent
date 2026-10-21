<script setup lang="ts">
import { useChatStore } from '../stores/chat'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ toggle: [] }>()

const store = useChatStore()
function newSession() {
  store.newSession()
}
function selectSession(id: string) {
  store.selectSession(id)
}
</script>

<template>
  <aside class="rail" :class="{ 'rail-expanded': open }" aria-label="会话栏">
    <button
      class="rail-collapse-btn"
      type="button"
      :aria-label="open ? '收起会话栏' : '展开会话栏'"
      :title="open ? '收起会话栏' : '展开会话栏'"
      :aria-expanded="open"
      aria-controls="session-rail-content"
      @click="emit('toggle')"
    >
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path v-if="open" d="m14 6-6 6 6 6" />
        <path v-else d="m10 6 6 6-6 6" />
      </svg>
    </button>
    <div v-if="open" id="session-rail-content" class="rail-content">
      <button class="new-btn" type="button" @click="newSession">
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
        新建导购
      </button>
      <h4>会话</h4>
      <div class="sessions">
        <div v-for="s in store.sessions" :key="s.id" class="session-row">
          <button
            class="session"
            :class="{ active: s.id === store.activeId }"
            @click="selectSession(s.id)"
          >
            <div class="t">{{ s.title }}</div>
            <div class="s">{{ s.subtitle }}</div>
          </button>
          <button class="session-del" title="删除会话" @click.stop="store.deleteSession(s.id)">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>
