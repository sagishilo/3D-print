<script setup lang="ts">
import { useToasts } from '@/stores/toasts'

import Icon from './Icon.vue'

const toasts = useToasts()
</script>

<template>
  <div class="toasts" role="region" aria-label="Notifications" aria-live="polite">
    <div v-for="t in toasts.items" :key="t.id" class="toast" :class="`tone-${t.tone}`" :role="t.tone === 'danger' ? 'alert' : 'status'">
      <span class="dot" aria-hidden="true" />
      <div class="body">
        <strong>{{ t.title }}</strong>
        <span v-if="t.text">{{ t.text }}</span>
      </div>
      <button type="button" class="close" aria-label="Dismiss" @click="toasts.dismiss(t.id)"><Icon name="x" :size="14" /></button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 50;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(380px, calc(100vw - 32px));
}
.toast {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.toast.tone-danger {
  border-color: var(--danger);
  background: var(--toast-danger);
}
.dot {
  margin-top: 5px;
}
.body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
  min-width: 0;
}
.body span {
  color: var(--muted);
  overflow-wrap: anywhere;
}
.close {
  background: none;
  border: 0;
  color: var(--muted);
  cursor: pointer;
  padding: 2px;
}
</style>
