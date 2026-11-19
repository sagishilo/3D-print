<script setup lang="ts">
// Shown on a screen while the dashboard focuses on one niche: says so, and offers to show everything again.
import { nicheColor } from '@/lib/niches'
import { useNiche } from '@/stores/niche'

defineProps<{ what?: string }>()
const niche = useNiche()
</script>

<template>
  <div v-if="niche.current" class="niche-bar" role="status" data-testid="niche-filter-bar">
    <span class="dot" :style="{ background: nicheColor(niche.active) }" />
    <span>Filtered: <b>{{ niche.name(niche.current) }}</b><template v-if="what"> · {{ what }}</template></span>
    <button type="button" class="link" data-testid="niche-show-all" @click="niche.set(null)">Show all</button>
  </div>
</template>

<style scoped>
.niche-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 12px;
  margin-bottom: var(--gap);
  border: 1px solid var(--accent-line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 13px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.link {
  margin-left: auto;
  background: none;
  border: 0;
  color: var(--accent);
  cursor: pointer;
  font: inherit;
  text-decoration: underline;
  padding: 0;
}
</style>
