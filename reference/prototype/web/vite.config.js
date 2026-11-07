import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// Vite config (plan §4 / Phase D):
// - React SPA, build into ../app/static (consumed by FastAPI StaticFiles)
// - dev server proxies /api to the local FastAPI on 127.0.0.1:54787
//   both REST (fetch) and SSE (EventSource) routes use the same proxy
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: '../app/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:54787',
        changeOrigin: false,
        ws: false,
        // Disable buffering so SSE events stream through the dev proxy
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache'
            proxyRes.headers['x-accel-buffering'] = 'no'
          })
        },
      },
    },
  },
})
