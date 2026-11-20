// Fonts are bundled (no requests to font CDNs: the console runs on the LAN, often without internet).
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/space-grotesk/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/600.css'
import './styles/tokens.css'
import './styles/base.css'

import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { applyLocale, i18n, initialLocale } from './i18n'
import { createAppRouter } from './router'

i18n.global.locale.value = initialLocale()
createApp(App).use(createPinia()).use(i18n).use(createAppRouter()).mount('#app')
applyLocale(initialLocale())          // Hebrew: right to left, and the older screens translated as they render

// The phone app (W2-D): the service worker keeps the shell for offline opening. Only in the built app, and only on
// HTTPS (a trusted certificate): browsers refuse service workers anywhere else.
if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* not trusted yet (the farm's certificate): the dashboard works the same, only not installable */
    })
  })
}
