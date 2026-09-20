<script setup lang="ts">
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()
const text = ref('')

async function send(): Promise<void> {
  const msg = text.value.trim()
  if (!msg) return
  text.value = ''
  await store.sendMessage(msg)
}
</script>

<template>
  <textarea
    v-model="text"
    placeholder="描述你的购物需求，例如：给女朋友买条简约的项链，预算 2000…"
    aria-label="输入购物需求"
    @keydown.enter.exact.prevent="send"
  ></textarea>
  <button class="send-btn" @click="send">发送</button>
</template>