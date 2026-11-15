<script setup lang="ts">
// A strip under the top bar when the gateway reports trouble (database, message bus, schema, cameras).
// Polls /api/health every 30 s (every 5 s while something is wrong), only while the tab is visible.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { type Health, healthProblems } from '@/lib/health'

const health = ref<Health | null>(null)
const answered = ref(true)
let timer: ReturnType<typeof setTimeout> | null = null

async function check() {
  if (!document.hidden) {
    try {
      const res = await fetch('/api/health', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      health.value = (await res.json()) as Health
      answered.value = true
    } catch {
      answered.value = false
    }
  }
  timer = setTimeout(check, problems.value.length ? 5_000 : 30_000)
}

// Not answered: say so. Answered (or not asked yet): whatever the gateway reports.
const problems = computed(() => (!answered.value ? healthProblems(null) : health.value ? healthProblems(health.value) : []))
const serious = computed(() => !answered.value || (health.value !== null && !health.value.ok))

onMounted(() => void check())
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div v-if="problems.length" class="health" :class="{ serious }" role="status" data-testid="health-banner">
    <Icon name="alert" :size="14" />
    <span>{{ problems.join(' · ') }}</span>
  </div>
</template>

<style scoped>
.health {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 8px 24px 0;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--warn-bg);
  color: var(--warn);
  font-size: 13px;
}
.health.serious {
  background: var(--danger-bg);
  color: var(--danger);
}
@media (max-width: 720px) {
  .health {
    margin: 8px 16px 0;
  }
}
</style>
