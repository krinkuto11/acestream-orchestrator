import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/panel/',
  build: {
    outDir: '../panel',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: undefined
      }
    }
  },
  server: {
    port: 3000,
    proxy: {
      '/engines': 'http://localhost:8000',
      '/streams': 'http://localhost:8000',
      '/vpn': 'http://localhost:8000'
    }
  }
})
