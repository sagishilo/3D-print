import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// `npm run dev` serves the dashboard on http://localhost:5173 and forwards the API and the live feed to the running
// stack (Caddy on https://localhost by default; set PRINTFARM_URL to use another host). The WebSocket Origin header is
// rewritten to the stack's own origin, so the gateway's origin check accepts the dev server.
const target = process.env.PRINTFARM_URL ?? 'https://localhost'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    fs: { allow: ['..'] }, // the user guide is imported from ../docs/USER_GUIDE_HE.md
    proxy: {
      '/api': { target, changeOrigin: true, secure: false },
      '/ws': { target, ws: true, changeOrigin: true, secure: false, rewriteWsOrigin: true },
    },
  },
  build: { sourcemap: true, chunkSizeWarningLimit: 600 },
  // css.include: the design test reads the theme tokens as text (tokens.css?raw); other CSS stays unprocessed
  test: { environment: 'jsdom', include: ['tests/**/*.spec.ts'], setupFiles: ['tests/setup.ts'], css: { include: [/tokens.css/] } },
})
