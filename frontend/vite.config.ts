import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    proxy: { '/api': { target: loadEnv(mode, process.cwd(), 'VITE_').VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true } },
  },
}))
