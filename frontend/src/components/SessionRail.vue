<script setup lang="ts">
import { useChatStore } from '../stores/chat'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ select: [] }>()

const store = useChatStore()
function newSession() {
  store.newSession()
  emit('select')
}
function selectSession(id: string) {
  store.selectSession(id)
  emit('select')
}
</script>

<template>
  <aside id="session-rail" class="rail" :class="{ 'rail-expanded': open }" aria-label="会话栏">
    <button class="new-btn" type="button" :title="open ? undefined : '新建导购'" :aria-label="open ? undefined : '新建导购'" @click="newSession">
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
      <span v-if="open">新建导购</span>
    </button>
    <h4 v-if="open">会话</h4>
    <div v-if="open" class="sessions">
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
  </aside>
</template>
