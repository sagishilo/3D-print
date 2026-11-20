<script setup lang="ts">
// One spool (design: material · brand, Active / Backup / Empty, grams, %, where, days left). While its printer prints,
// the grams go down live from the printer's progress.
import { computed } from 'vue'

import StatusPill from '@/components/ui/StatusPill.vue'
import { type Spool, where } from '@/lib/filament'
import type { Tone } from '@/lib/types'

const props = defineProps<{ spool: Spool; inPrint?: number; dailyGrams?: number | null }>()
defineEmits<{ open: [id: string] }>()

const live = computed(() => Math.max(props.spool.remaining_grams - (props.inPrint ?? 0), 0))
const pct = computed(() => (props.spool.initial_grams > 0 ? Math.round((live.value / props.spool.initial_grams) * 100) : 0))
const label = computed(() => (props.spool.status === 'loaded' ? 'Active' : props.spool.status === 'empty' ? 'Empty' : 'Backup'))
const tone = computed<Tone>(() => (props.spool.status === 'empty' ? 'danger' : props.spool.status === 'loaded' ? (pct.value < 15 ? 'warn' : 'ok') : 'idle'))
const daysLeft = computed(() => {
  if (props.spool.status !== 'loaded') return props.spool.status === 'empty' ? 'empty' : '—'
  return props.dailyGrams ? `${(live.value / props.dailyGrams).toFixed(1)} d` : '—'
})
</script>

<template>
  <button type="button" class="spool" :class="spool.status" :data-testid="`spool-${spool.spool_id}`" data-tour="spool-card" @click="$emit('open', spool.spool_id)">
    <div class="row">
      <span class="name">{{ spool.material }} · {{ spool.color }}</span>
      <StatusPill :tone="tone" :label="label" />
    </div>
    <span class="brand muted">{{ spool.brand || 'unknown brand' }} · {{ spool.spool_id }}</span>
    <div class="grams">
      <b class="mono">{{ Math.round(live) }} g</b>
      <span class="mono muted">{{ pct }}%</span>
    </div>
    <div class="bar" role="meter" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100" :aria-label="`${spool.spool_id} remaining`">
      <div :style="{ width: `${pct}%` }" :class="{ low: pct < 15 }" />
    </div>
    <div class="row muted small">
      <span>{{ where(spool) }}</span>
      <span>{{ daysLeft }}</span>
    </div>
    <span v-if="inPrint" class="printing small">printing · −{{ Math.round(inPrint) }} g so far</span>
  </button>
</template>

<style scoped>
.spool {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: start;
  padding: 12px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font: inherit;
}
.spool:hover {
  border-color: var(--border-strong);
}
.spool.empty {
  opacity: 0.6;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.name {
  font-weight: 600;
}
.brand,
.small {
  font-size: 12px;
}
.grams {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.grams b {
  font-size: 20px;
}
.bar {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}
.bar > div {
  height: 100%;
  background: var(--ok);
  transition: width 0.4s ease;
}
.bar > div.low {
  background: var(--warn);
}
.printing {
  color: var(--accent);
}
</style>
