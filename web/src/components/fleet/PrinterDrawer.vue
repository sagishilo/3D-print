<script setup lang="ts">
// Printer details (the design's "Details"): camera and telemetry, deep hardware, calibration utilities and the
// printer's own history. Live values come from the printers store; history reloads when this printer's events arrive.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import CameraView from '@/components/live/CameraView.vue'
import TelemetryReplay from '@/components/live/TelemetryReplay.vue'
import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import TabBar from '@/components/ui/TabBar.vue'
import { errorText } from '@/lib/api'
import { dateTime, duration, title } from '@/lib/format'
import type { PrinterDetail } from '@/lib/types'
import { useLive } from '@/stores/live'
import { displayStatus, STATUS_TONE, usePrinters } from '@/stores/printers'

import CalibrationPanel from './CalibrationPanel.vue'
import HardwarePanel from './HardwarePanel.vue'

const props = defineProps<{ printerId: string }>()
const emit = defineEmits<{ close: [] }>()

const printers = usePrinters()
const live = useLive()
const detail = ref<PrinterDetail | null>(null)
const error = ref('')
const tab = ref('overview')
const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'hardware', label: 'Hardware' },
  { id: 'calibration', label: 'Calibration' },
  { id: 'history', label: 'History' },
]

const printer = computed(() => printers.get(props.printerId) ?? detail.value?.printer ?? null)
const status = computed(() => (printer.value ? displayStatus(printer.value) : 'offline'))
const successRate = computed(() => {
  const st = detail.value?.stats
  return st && st.prints_7d ? `${Math.round((st.succeeded_7d / st.prints_7d) * 100)}%` : '—'
})

async function load() {
  try {
    detail.value = await printers.detail(props.printerId)
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  }
}

let reload: ReturnType<typeof setTimeout> | null = null
function reloadSoon() {
  if (reload) clearTimeout(reload)
  reload = setTimeout(() => void load(), 400)
}

watch(() => props.printerId, () => {
  detail.value = null
  void load()
}, { immediate: true })

// New history for this printer (calibrations, pauses, failures, prints) -> refresh the tables. Every entry that
// arrived since the last look counts (several can land in one batch).
let seen = new Set(live.feed.map((e) => e.key))
watch(
  () => live.feed,
  (feed) => {
    const fresh = feed.filter((e) => !seen.has(e.key))
    seen = new Set(feed.map((e) => e.key))
    if (fresh.some((e) => !e.replayed && e.d.printerId === props.printerId && /^printer\.(calibration|pause|failure|print)\./.test(e.d.kind))) {
      reloadSoon()
    }
  },
)

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  if (reload) clearTimeout(reload)
})

function took(start: string, end: string | null): string {
  return end ? duration((new Date(end).getTime() - new Date(start).getTime()) / 1000) : 'running'
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <aside class="drawer" role="dialog" :aria-label="`${printerId} details`" aria-modal="true" data-testid="printer-drawer">
      <header class="head">
        <div class="title">
          <h2>{{ printerId }}</h2>
          <StatusPill v-if="printer" :tone="STATUS_TONE[status]" :label="title(status)" />
          <span v-if="printer" class="chip">{{ printer.protocol }}</span>
          <span v-if="printer?.model" class="chip">{{ printer.model.toUpperCase() }}</span>
          <span v-if="printer?.simulated" class="chip warn">SIM</span>
        </div>
        <button type="button" class="icon-btn" aria-label="Close details" @click="emit('close')"><Icon name="x" /></button>
      </header>

      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <TabBar v-model="tab" :tabs="TABS" :label="`${printerId} details`" />

      <template v-if="printer && tab === 'overview'">
        <CameraView :printer-id="printer.id" :available="printer.camera" :protocol="printer.protocol" :simulated="printer.simulated" :height="300" />
        <div class="facts">
          <div><span>Bed</span><b class="mono">{{ printer.bed_mm[0] }} × {{ printer.bed_mm[1] }} mm</b></div>
          <div><span>Filament</span><b>{{ printer.material }} {{ printer.color }}</b></div>
          <div><span>Prints (7 days)</span><b class="mono">{{ detail?.stats.prints_7d ?? '—' }}</b></div>
          <div><span>Success</span><b class="mono">{{ successRate }}</b></div>
          <div><span>Print time (7 days)</span><b class="mono">{{ detail ? `${detail.stats.print_hours_7d} h` : '—' }}</b></div>
          <div><span>Bed</span><b :class="printer.bed_clear ? 'text-ok' : 'text-warn'">{{ printer.bed_clear ? 'clear' : 'needs clearing' }}</b></div>
        </div>
        <TelemetryReplay :printer-id="printer.id" :closable="false" />
      </template>

      <HardwarePanel v-else-if="printer && tab === 'hardware'" :printer="printer" />

      <template v-else-if="tab === 'calibration'">
        <CalibrationPanel v-if="detail" :detail="detail" @requested="reloadSoon" />
        <p v-else class="empty">Loading…</p>
      </template>

      <template v-else-if="tab === 'history' && detail">
        <h3>Prints</h3>
        <div class="table-wrap">
          <table class="data compact">
            <thead><tr><th scope="col">Plate</th><th scope="col">Started</th><th scope="col">Took</th><th scope="col">Result</th></tr></thead>
            <tbody>
              <tr v-for="p in detail.sessions" :key="p.plate_id">
                <td class="mono">{{ p.plate_id }}</td>
                <td class="num">{{ dateTime(p.started_at) }}</td>
                <td class="num">{{ took(p.started_at, p.finished_at) }}</td>
                <td>
                  <StatusPill :tone="p.succeeded == null ? 'accent' : p.succeeded ? 'ok' : 'danger'" :label="p.succeeded == null ? 'Printing' : p.succeeded ? 'Finished' : 'Failed'" />
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!detail.sessions.length" class="empty">No prints yet.</p>
        </div>

        <h3>Verified pauses</h3>
        <div class="table-wrap">
          <table class="data compact">
            <thead><tr><th scope="col">When</th><th scope="col">Reason</th><th scope="col">By</th><th scope="col">Result</th></tr></thead>
            <tbody>
              <tr v-for="(c, i) in detail.pause_checks" :key="i">
                <td class="num">{{ dateTime(c.checked_at) }}</td>
                <td class="wrap">{{ c.reason }}</td>
                <td>{{ c.requested_by }}</td>
                <td>
                  <StatusPill :tone="c.confirmed ? 'ok' : 'danger'" :label="c.confirmed ? `Paused in ${((c.latency_ms ?? 0) / 1000).toFixed(1)} s` : 'NOT confirmed'" />
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!detail.pause_checks.length" class="empty">No pauses.</p>
        </div>

        <h3>Failures</h3>
        <div class="table-wrap">
          <table class="data compact">
            <thead><tr><th scope="col">When</th><th scope="col">Detector</th><th scope="col">Severity</th><th scope="col">Message</th><th scope="col">Action</th></tr></thead>
            <tbody>
              <tr v-for="(f, i) in detail.failures" :key="i">
                <td class="num">{{ dateTime(f.detected_at) }}</td>
                <td>{{ f.detector.replaceAll('_', ' ') }}</td>
                <td><StatusPill :tone="f.severity === 'critical' ? 'danger' : f.severity === 'warning' ? 'warn' : 'idle'" :label="title(f.severity)" /></td>
                <td class="wrap">{{ f.message }}</td>
                <td class="wrap">{{ f.action_taken }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!detail.failures.length" class="empty">No failures detected.</p>
        </div>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  z-index: 40;
  display: flex;
  justify-content: flex-end;
}
.drawer {
  width: min(760px, 100vw);
  height: 100%;
  overflow-y: auto;
  background: var(--surface);
  border-left: 1px solid var(--border);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
/* In a scrolling flex column, a child that scrolls itself (the tables' wrappers) would shrink and clip its rows. */
.drawer > * {
  flex-shrink: 0;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.title h2 {
  font: 700 20px var(--font-display);
}
.facts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
.facts div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
}
.facts span {
  font-size: 12px;
  color: var(--muted);
}
h3 {
  font: 600 13px var(--font);
  margin-top: 4px;
}
.compact {
  min-width: 0;
}
.compact td {
  white-space: nowrap;
}
.compact td.wrap {
  white-space: normal;
  font-size: 12px;
}
.error {
  color: var(--danger);
  font-size: 13px;
}
</style>
