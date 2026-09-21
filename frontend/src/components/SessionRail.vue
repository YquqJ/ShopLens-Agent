<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()

const prefs = computed(() => {
  const last = store.activeSession.turns
    .map((t) => t.preferences)
    .filter(Boolean)
    .at(-1)
  return last ?? null
})

const prefTags = computed(() => {
  if (!prefs.value) return []
  const tags: string[] = []
  if (prefs.value.scene) tags.push(prefs.value.scene)
  if (prefs.value.material) tags.push(prefs.value.material)
  if (prefs.value.budget) tags.push(`预算 ≤ ¥${prefs.value.budget}`)
  if (prefs.value.style) tags.push(prefs.value.style)
  return tags
})
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
    <div class="profile-card">
      <h5>已理解偏好</h5>
      <div class="pf-tags">
        <span v-if="!prefTags.length" style="font-size:12.5px;color:var(--muted-fg)">对话后自动提取</span>
        <span v-for="t in prefTags" :key="t" class="pf-tag">{{ t }}</span>
      </div>
    </div>
  </aside>
</template>