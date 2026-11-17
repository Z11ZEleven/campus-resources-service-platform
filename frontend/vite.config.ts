import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const proxy = {
    '/api': {
      target: loadEnv(mode, process.cwd(), 'VITE_').VITE_API_PROXY_TARGET || 'http://127.0.0.1:8001',
      changeOrigin: false,
    },
  }
  return {
    plugins: [vue()],
    server: { host: '127.0.0.1', proxy },
    // Production build preview: far fewer requests than the dev server, suitable for phones.
    preview: { host: '0.0.0.0', port: 4173, proxy },
  }
})
