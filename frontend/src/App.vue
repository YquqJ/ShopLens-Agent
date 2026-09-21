<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useChatStore } from './stores/chat'
import SessionRail from './components/SessionRail.vue'
import ChatView from './components/ChatView.vue'
import Composer from './components/Composer.vue'
import TraceDrawer from './components/TraceDrawer.vue'
import DashboardView from './components/DashboardView.vue'

type ViewName = 'chat' | 'dashboard'
const view = ref<ViewName>('chat')
const drawerOpen = ref(true)
const store = useChatStore()
// 启动即从后端恢复会话列表与最近对话（刷新后不留空）
onMounted(() => { void store.hydrate() })
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg>
        </span>
        ShopLens <small>Concierge</small>
      </div>
      <nav class="nav" aria-label="主导航">
        <button class="nav-item" :class="{ active: view === 'chat' }" @click="view = 'chat'">导购</button>
        <button class="nav-item" :class="{ active: view === 'dashboard' }" @click="view = 'dashboard'">转化看板</button>
      </nav>
      <div class="spacer"></div>
      <button v-if="view === 'chat'" class="trace-btn" :aria-expanded="drawerOpen" @click="drawerOpen = !drawerOpen">
        <span class="pulse"></span> Agent Trace
      </button>
    </header>

    <div class="body" :class="{ 'drawer-closed': !drawerOpen, 'dash-active': view === 'dashboard' }">
      <SessionRail v-if="view === 'chat'" />

      <div class="main-col">
        <div class="content">
          <section class="view" :class="{ active: view === 'chat' }">
            <ChatView />
          </section>
          <section class="view" :class="{ active: view === 'dashboard' }">
            <DashboardView :active="view === 'dashboard'" />
          </section>
        </div>
        <div v-if="view === 'chat'" class="composer">
          <div class="composer-inner">
            <Composer />
          </div>
        </div>
      </div>

      <TraceDrawer v-if="view === 'chat'" />
    </div>
  </div>
</template>