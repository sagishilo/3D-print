<script setup lang="ts">
// "Failure detection & event log" (design): printer and plate events from the EVENTS stream (up to 7 days), with
// printer and severity filters; new events from the live feed are added at the top as they happen.
import { computed, ref, watch } from 'vue'

import StatusPill from '@/components/ui/StatusPill.vue'
import { errorText } from '@/lib/api'
import { describe } from '@/lib/events'
import { SEVERITY_RANK, severityOf, sourceLabel } from '@/lib/fleet'
import { dateTime } from '@/lib/format'
import type { LogItem, Severity, Tone } from '@/lib/types'
import { useLive } from '@/stores/live'
import { usePrinters } from '@/stores/printers'

const emit = defineEmits<{ open: [printerId: string] }>()

const printers = usePrinters()
const live = useLive()
const printer = ref<string>('')
const minSeverity = ref<Severity>('info')
const hours = ref(24)
const items = ref<LogItem[]>([])
const loading = ref(false)
const error = ref('')

const TONE: Record<Severity, Tone> = { info: 'idle', warning: 'warn', critical: 'danger' }
const LABEL: Record<Severity, string> = { info: 'Info', warning: 'Warning', critical: 'Critical' }

async function load() {
  loading.value = true
  try {
    items.value = (await printers.log({ printer: printer.value || null, minSeverity: minSeverity.value, hours: hours.value })).items
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
watch([printer, minSeverity, hours], load, { immediate: true })

// Live additions: printer and plate events from the feed that pass the filters.
let seen = new Set(live.feed.map((e) => e.key))
watch(
  () => live.feed,
  (feed) => {
    const fresh = feed.filter((e) => !seen.has(e.key) && !e.replayed)
    seen = new Set(feed.map((e) => e.key))
    const known = new Set(items.value.map((i) => `${i.stream}:${i.seq}`))
    const add: LogItem[] = []
    for (const e of fresh) {
      if (!/^(printer|fleet\.plate)\./.test(e.d.kind) || known.has(e.key)) continue
      const data = (e.item.data.data ?? {}) as Record<string, unknown>
      const severity = severityOf(e.d.kind, data)
      if (SEVERITY_RANK[severity] < SEVERITY_RANK[minSeverity.value]) continue
      if (printer.value && e.d.printerId !== printer.value) continue
      add.push({ ...e.item, severity, printer_id: e.d.printerId ?? null, time: e.at, source: e.item.data.source })
    }
    if (add.length) items.value = [...add, ...items.value].slice(0, 1000) // the feed is newest first, like the log
  },
)

const rows = computed(() => items.value.map((i) => ({ i, d: describe(i.data) })))
</script>

<template>
  <section class="panel" data-testid="fleet-log">
    <div class="panel-head">
      <h2>Failure detection &amp; event log</h2>
      <div class="filters">
        <label class="sr-only" for="log-printer">Printer</label>
        <select id="log-printer" v-model="printer" class="select">
          <option value="">All printers</option>
          <option v-for="p in printers.list" :key="p.id" :value="p.id">{{ p.id }}</option>
        </select>
        <label class="sr-only" for="log-severity">Severity</label>
        <select id="log-severity" v-model="minSeverity" class="select">
          <option value="info">All severities</option>
          <option value="warning">Warnings and critical</option>
          <option value="critical">Critical only</option>
        </select>
        <label class="sr-only" for="log-hours">Period</label>
        <select id="log-hours" v-model.number="hours" class="select">
          <option :value="24">last 24 h</option>
          <option :value="72">last 3 days</option>
          <option :value="168">last 7 days</option>
        </select>
      </div>
    </div>
    <p v-if="error" class="panel-note text-danger" role="alert">{{ error }}</p>
    <div class="table-wrap">
      <table class="data log">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Printer</th>
            <th scope="col">Severity</th>
            <th scope="col">Event</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="{ i, d } in rows" :key="`${i.stream}:${i.seq}`" :class="`sev-${i.severity}`">
            <td class="num">{{ dateTime(i.time) }}</td>
            <td>
              <button v-if="i.printer_id" type="button" class="link" @click="emit('open', i.printer_id)">{{ i.printer_id }}</button>
              <span v-else class="muted">—</span>
            </td>
            <td><StatusPill :tone="TONE[i.severity]" :label="LABEL[i.severity]" /></td>
            <td class="event">{{ d.text }}</td>
            <td class="muted">{{ sourceLabel(i.source) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length && !loading" class="empty">No printer events in this period{{ minSeverity !== 'info' ? ' at this severity' : '' }}.</p>
    </div>
  </section>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.filters .select {
  min-height: 32px;
  font-size: 13px;
}
.log {
  min-width: 640px;
}
.log td {
  white-space: nowrap;
}
.log td.event {
  white-space: normal;
}
.sev-critical td.event {
  color: #ffb4ae;
}
.link {
  background: none;
  border: 0;
  padding: 0;
  color: var(--accent);
  cursor: pointer;
  font: inherit;
}
.link:hover {
  color: var(--accent-hover);
}
</style>
