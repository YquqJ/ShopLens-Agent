import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: {
      // 后端 FastAPI 跑在 127.0.0.1:8000，SSE/转化接口经 dev 代理转发，规避开发期跨域
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})