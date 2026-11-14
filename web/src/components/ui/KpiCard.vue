<script setup lang="ts">
import type { Tone } from '@/lib/types'

withDefaults(defineProps<{ label: string; value: string; sub?: string; subTone?: Tone }>(), { sub: '', subTone: 'idle' })
</script>

<template>
  <div class="kpi">
    <span class="kpi-label">{{ label }}</span>
    <span class="kpi-value">{{ value }}</span>
    <span v-if="sub" class="kpi-sub" :class="`sub-${subTone}`">{{ sub }}</span>
  </div>
</template>

<style scoped>
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.kpi-label {
  font: 500 12px var(--font);
  color: var(--muted);
}
.kpi-value {
  font: 600 24px var(--font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kpi-sub {
  font: 500 12px var(--font);
  color: var(--muted);
}
.sub-ok {
  color: var(--ok);
}
.sub-warn {
  color: var(--warn);
}
.sub-danger {
  color: var(--danger);
}
.sub-accent {
  color: var(--accent);
}
</style>
