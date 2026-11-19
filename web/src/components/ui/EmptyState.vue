<script setup lang="ts">
// The one empty state of the dashboard: what would be here, why it is empty, and the next step (an action button or
// a link). Every list that can be empty uses it, so an empty screen always says what to do.
import { RouterLink } from 'vue-router'

import Icon from './Icon.vue'

const props = defineProps<{
  icon?: string
  title: string
  text?: string
  /** The next step: a link (to) or a button (emits "action"). */
  actionLabel?: string
  to?: string
}>()
const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <div class="empty-state" data-testid="empty-state" role="status">
    <span class="icon" aria-hidden="true"><Icon :name="props.icon ?? 'inbox'" :size="22" /></span>
    <b class="title">{{ props.title }}</b>
    <p v-if="props.text" class="text">{{ props.text }}</p>
    <slot />
    <RouterLink v-if="props.actionLabel && props.to" :to="props.to" class="btn btn-primary btn-sm" data-testid="empty-action">
      {{ props.actionLabel }}
    </RouterLink>
    <button v-else-if="props.actionLabel" type="button" class="btn btn-primary btn-sm" data-testid="empty-action" @click="emit('action')">
      {{ props.actionLabel }}
    </button>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: calc(var(--pad-panel) * 2) var(--pad-panel);
  text-align: center;
}
.icon {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-bg);
  color: var(--accent);
}
.title {
  font: 600 15px var(--font);
}
.text {
  max-width: 52ch;
  color: var(--muted);
  font-size: 13px;
}
</style>
