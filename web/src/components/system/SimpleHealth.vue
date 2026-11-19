<script setup lang="ts">
// Simple mode's view of the system: one line that says whether everything runs, and in plain words what does not.
// The technical panels (agents, NATS, dead letters, automation settings) are in Expert mode.
import { computed } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { useSystem } from '@/stores/system'

const system = useSystem()

const problems = computed(() => {
  const out: string[] = []
  const o = system.overview
  if (o && !o.postgres.ok) out.push('The database does not answer: nothing can be saved.')
  if (o && !o.nats.ok) out.push('The message system does not answer: printers and orders do not update.')
  if (o && !o.go2rtc.ok) out.push('The camera relay does not answer: camera tiles stay dark.')
  for (const a of system.coreAgents.filter((x) => x.status !== 'healthy'))
    out.push(`${a.label} is ${a.status === 'late' ? 'slow to answer' : 'not running'}.`)
  if (system.dlqTotal) out.push(`${system.dlqTotal} message${system.dlqTotal === 1 ? '' : 's'} could not be handled and wait for a technical check.`)
  return out
})
</script>

<template>
  <section class="panel simple-health" :class="problems.length ? 'bad' : 'good'" data-testid="simple-health" data-tour="simple-health">
    <div class="head">
      <Icon :name="problems.length ? 'alert' : 'check'" :size="20" />
      <h2>{{ problems.length ? `${problems.length} thing${problems.length === 1 ? '' : 's'} need${problems.length === 1 ? 's' : ''} attention` : 'Everything is running' }}</h2>
    </div>
    <ul v-if="problems.length">
      <li v-for="p in problems" :key="p">{{ p }}</li>
    </ul>
    <p v-else class="muted">All services and agents answer. Printers, orders and stock update on their own.</p>
    <p class="muted small">
      Technical details (agents, NATS, dead letters, automation settings) are in <b>Expert</b> mode: your name in the top bar ▸ Mode.
      If something stays red, ask the person who runs the system.
    </p>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.head h2 {
  font: 700 17px var(--font-display);
}
.good .head {
  color: var(--ok);
}
.bad {
  border-color: var(--danger);
}
.bad .head {
  color: var(--danger);
}
ul {
  margin: 0;
  padding-inline-start: 20px;
}
.small {
  font-size: 12px;
}
</style>
