<script setup lang="ts">
// The live event feed with filters and replay of stored history (EVENTS keeps 30 days).
import { computed, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import type { Category } from '@/lib/events'
import { clock, dateTime } from '@/lib/format'
import { type FeedEntry, useLive } from '@/stores/live'

const props = withDefaults(defineProps<{ printerId?: string | null; limit?: number }>(), { printerId: null, limit: 200 })

const live = useLive()
const filter = ref<'all' | Category | 'safety'>('all')
const minutes = ref(60)
const FILTERS: { id: typeof filter.value; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'printer', label: 'Printers' },
  { id: 'order', label: 'Orders' },
  { id: 'stock', label: 'Stock' },
  { id: 'money', label: 'Money' },
  { id: 'social', label: 'Social' },
  { id: 'system', label: 'System' },
  { id: 'safety', label: 'Safety' },
]
const RANGES = [
  { m: 15, label: 'last 15 min' },
  { m: 60, label: 'last hour' },
  { m: 24 * 60, label: 'last 24 h' },
  { m: 7 * 24 * 60, label: 'last 7 days' },
]

function keep(e: FeedEntry): boolean {
  if (props.printerId && e.d.printerId !== props.printerId) return false
  if (filter.value === 'all') return true
  if (filter.value === 'safety') return e.d.tone === 'danger' || e.d.kind.startsWith('printer.pause') || e.d.kind === 'printer.failure.detected'
  return e.d.category === filter.value
}
const entries = computed(() => live.feed.filter(keep).slice(0, props.limit))
const replayState = computed(() => {
  const r = live.replay
  if (!r) return ''
  if (!r.done) return 'Replaying…'
  return `Replayed ${r.count} event${r.count === 1 ? '' : 's'} since ${dateTime(r.since.toISOString())}${r.truncated ? ' (newest only)' : ''}`
})
</script>

<template>
  <section class="panel feed">
    <div class="panel-head">
      <h2>Live events{{ printerId ? ` · ${printerId}` : '' }}</h2>
      <div class="replay">
        <label class="sr-only" for="replay-range">Replay range</label>
        <select id="replay-range" v-model.number="minutes" class="select">
          <option v-for="r in RANGES" :key="r.m" :value="r.m">{{ r.label }}</option>
        </select>
        <button type="button" class="btn btn-sm" data-testid="replay" @click="live.replaySince(minutes)">
          <Icon name="history" :size="14" />Replay
        </button>
      </div>
    </div>
    <div class="filters" role="group" aria-label="Filter events">
      <button
        v-for="f in FILTERS"
        :key="f.id"
        type="button"
        class="filter"
        :class="{ on: filter === f.id }"
        :aria-pressed="filter === f.id"
        @click="filter = f.id"
      >
        {{ f.label }}
      </button>
    </div>
    <p v-if="replayState" class="panel-note" role="status">{{ replayState }}</p>
    <ol class="items" aria-live="off">
      <li v-for="e in entries" :key="e.key" :class="`tone-${e.d.tone}`">
        <span class="time mono">{{ clock(e.at) }}</span>
        <span class="dot" aria-hidden="true" />
        <span class="text">
          {{ e.d.text }}
          <span v-if="e.replayed" class="chip">replayed</span>
        </span>
        <span class="kind mono">{{ e.item.topic }}</span>
      </li>
    </ol>
    <p v-if="!entries.length" class="empty">
      No events yet{{ filter !== 'all' ? ' for this filter' : '' }}. New ones appear here as they happen; Replay loads stored history.
    </p>
  </section>
</template>

<style scoped>
.feed {
  max-height: 640px;
}
.replay {
  display: flex;
  gap: 8px;
  align-items: center;
}
.replay .select {
  min-height: 32px;
  font-size: 13px;
}
.filters {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.filter {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted);
  font: 500 12px var(--font);
  cursor: pointer;
}
.filter.on {
  background: var(--surface-2);
  color: var(--text);
  border-color: #33404d;
}
.items {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.items li {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  grid-template-areas: 'time dot text' '. . kind';
  column-gap: 10px;
  padding: 8px 4px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.time {
  grid-area: time;
  font-size: 12px;
  color: var(--muted);
}
.items .dot {
  grid-area: dot;
  margin-top: 6px;
}
.text {
  grid-area: text;
  overflow-wrap: anywhere;
}
.kind {
  grid-area: kind;
  font-size: 11px;
  color: var(--idle);
}
.tone-danger .text {
  color: #ffb4ae;
}
</style>
