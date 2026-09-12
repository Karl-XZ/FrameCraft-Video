import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendTarget = process.env.FRAMECRAFT_BACKEND_URL ?? 'http://127.0.0.1:8022'

export default defineConfig(({ command }) => {
  const publicBase = process.env.FRAMECRAFT_PUBLIC_BASE ?? (command === 'build' ? '/FrameCraft/' : '/')

  return {
    base: publicBase,
    plugins: [react()],
    server: {
      port: Number(process.env.FRAMECRAFT_FRONTEND_PORT ?? 5174),
      allowedHosts: ['.trycloudflare.com', 'localhost', '127.0.0.1'],
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: Number(process.env.FRAMECRAFT_FRONTEND_PORT ?? 4174),
      allowedHosts: ['.trycloudflare.com', 'localhost', '127.0.0.1'],
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
