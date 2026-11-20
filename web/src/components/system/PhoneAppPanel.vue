<script setup lang="ts">
// The phone app (W2-D): the dashboard installs on a phone as an app (PWA) once the phone trusts this farm's own
// HTTPS certificate. LAN only by default; away from the farm it works over Tailscale with no change (every link is
// relative). Words from the i18n catalogue.
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const origin = typeof location !== 'undefined' ? location.origin : ''
const secure = typeof window !== 'undefined' && window.isSecureContext
const app = ref<'installed' | 'ready' | 'untrusted'>('untrusted')

onMounted(async () => {
  if (window.matchMedia?.('(display-mode: standalone)').matches) app.value = 'installed'
  else if (secure && 'serviceWorker' in navigator && (await navigator.serviceWorker.getRegistration().catch(() => undefined))) app.value = 'ready'
})
</script>

<template>
  <section class="panel phone" data-testid="phone-app" data-tour="phone-app">
    <div class="panel-head"><h2>{{ t('phone.title') }}</h2><span class="panel-note">{{ t('phone.subtitle') }}</span></div>
    <p class="state small" :class="app" data-testid="phone-state">{{ t(`phone.state.${app}`) }}</p>
    <ol class="steps">
      <li>
        <b>{{ t('phone.step1') }}</b>
        <span class="muted small">{{ t('phone.step1Help') }}</span>
        <a class="btn btn-sm" href="/ca.crt" download="printfarm-ca.crt" data-testid="ca-download">{{ t('phone.download') }}</a>
      </li>
      <li>
        <b>{{ t('phone.step2') }}</b>
        <span class="muted small">{{ t('phone.android') }}</span>
        <span class="muted small">{{ t('phone.iphone') }}</span>
      </li>
      <li>
        <b>{{ t('phone.step3') }}</b>
        <span class="mono small" translate="no" data-testid="phone-address">{{ origin }}</span>
        <span class="muted small">{{ t('phone.step3Help') }}</span>
      </li>
    </ol>
    <p class="muted small">{{ t('phone.away') }}</p>
  </section>
</template>

<style scoped>
.steps {
  margin: 0 0 8px;
  padding-inline-start: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.steps li {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}
.state {
  margin: 0 0 8px;
  font-weight: 600;
  color: var(--muted);
}
.state.installed,
.state.ready {
  color: var(--ok);
}
</style>
