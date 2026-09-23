<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()

const prefTags = computed(() => {
  const prefs = store.profilePreferences
  const tags: string[] = []
  if (prefs.preferred_colors?.length) tags.push(`颜色：${prefs.preferred_colors.join('、')}`)
  if (prefs.preferred_styles?.length) tags.push(`风格：${prefs.preferred_styles.join('、')}`)
  if (prefs.preferred_materials?.length) tags.push(`材质：${prefs.preferred_materials.join('、')}`)
  if (prefs.avoid_preferences?.length) tags.push(`避雷：${prefs.avoid_preferences.join('、')}`)
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
      <h5>跨会话偏好记忆</h5>
      <div class="pf-tags">
        <span v-if="!prefTags.length" style="font-size:12.5px;color:var(--muted-fg)">尚无已保存偏好</span>
        <span v-for="t in prefTags" :key="t" class="pf-tag">{{ t }}</span>
      </div>
    </div>
  </aside>
</template>
