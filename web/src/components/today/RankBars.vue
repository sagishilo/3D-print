<script setup lang="ts">
// Horizontal bars for "where is the work / the money": each row is labelled directly (so identity never rests on
// colour), with its value at the end. One hue by default; a row may carry its own colour token (niches).
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

const props = defineProps<{
  rows: { key: string; label: string; value: number; color?: string; to?: string }[]
  format: (v: number) => string
  testid?: string
  empty?: string
}>()
const max = computed(() => Math.max(1, ...props.rows.map((r) => r.value)))
</script>

<template>
  <ul class="rank" :data-testid="testid" role="list">
    <li v-for="r in rows" :key="r.key" :data-testid="testid ? `${testid}-${r.key}` : undefined" :title="`${r.label}: ${format(r.value)}`">
      <RouterLink v-if="r.to" class="label" :to="r.to">{{ r.label }}</RouterLink>
      <span v-else class="label">{{ r.label }}</span>
      <span class="track"><span class="bar" :style="{ width: `${(r.value / max) * 100}%`, background: r.color ?? 'var(--accent)' }" /></span>
      <span class="val">{{ format(r.value) }}</span>
    </li>
    <li v-if="!rows.length" class="muted small">{{ empty ?? 'Nothing yet.' }}</li>
  </ul>
</template>

<style scoped>
.rank {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
li {
  display: grid;
  grid-template-columns: 96px 1fr auto;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.label {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-decoration: none;
}
a.label:hover {
  text-decoration: underline;
}
.track {
  height: 10px;
  background: var(--surface-2);
  border-radius: 5px;
  overflow: hidden;
}
.bar {
  display: block;
  height: 100%;
  min-width: 2px;
  border-radius: 0 5px 5px 0;
}
.val {
  font-variant-numeric: tabular-nums;
  color: var(--text);
  min-width: 28px;
  text-align: end;
}
</style>
