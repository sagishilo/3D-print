<script setup lang="ts">
// Replay of one printer's last hour from the TELEMETRY stream: temperatures and progress over time, a scrubber to
// read the printer's state at any moment (or play it back), and markers for failures, pauses and plate events.
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { errorText } from '@/lib/api'
import { clock, pct, temp, title } from '@/lib/format'
import type { TelemetrySample, Tone } from '@/lib/types'
import { useLive } from '@/stores/live'
import { STATUS_TONE, usePrinters } from '@/stores/printers'

const props = withDefaults(defineProps<{ printerId: string; closable?: boolean }>(), { closable: true })
defineEmits<{ close: [] }>()

const printers = usePrinters()
const live = useLive()
const minutes = ref(30)
const samples = ref<TelemetrySample[]>([])
const cursor = ref(0)
const following = ref(true)
const loading = ref(false)
const error = ref('')
let player: ReturnType<typeof setInterval> | null = null

const W = 600
const H = 200
const TOP = 16
const BOTTOM = 188

async function load() {
  loading.value = true
  stop()
  try {
    const r = await printers.history(props.printerId, minutes.value)
    samples.value = r.samples
    cursor.value = Math.max(r.samples.length - 1, 0)
    following.value = true
    error.value = ''
    live.replaySince(minutes.value, ['evt.printer.>', 'evt.fleet.plate.>']) // markers for this window
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
watch(() => [props.printerId, minutes.value], load, { immediate: true })

// Live samples extend the chart while the cursor is at the end.
watch(
  () => printers.get(props.printerId)?.state,
  (s) => {
    if (!s || !samples.value.length) return
    const last = samples.value[samples.value.length - 1]
    if (s.observed_at <= last.observed_at) return
    samples.value = [...samples.value, { ...s }].slice(-900)
    if (following.value) cursor.value = samples.value.length - 1
  },
)

const times = computed(() => samples.value.map((s) => new Date(s.observed_at).getTime()))
const t0 = computed(() => times.value[0] ?? 0)
const t1 = computed(() => times.value[times.value.length - 1] ?? 1)
const maxTemp = computed(() =>
  Math.max(260, ...samples.value.map((s) => Math.max(s.nozzle_temp ?? 0, s.nozzle_target ?? 0, s.bed_temp ?? 0)).map((t) => t + 10)),
)
const x = (t: number) => (t1.value === t0.value ? W : ((t - t0.value) / (t1.value - t0.value)) * W)
const yT = (v: number) => BOTTOM - (v / maxTemp.value) * (BOTTOM - TOP)

function line(pick: (s: TelemetrySample) => number | null | undefined): string {
  return samples.value
    .map((s, i) => {
      const v = pick(s)
      return v == null ? null : `${x(times.value[i]).toFixed(1)},${yT(v).toFixed(1)}`
    })
    .filter(Boolean)
    .join(' ')
}
const nozzle = computed(() => line((s) => s.nozzle_temp))
const nozzleTarget = computed(() => line((s) => s.nozzle_target))
const bed = computed(() => line((s) => s.bed_temp))
const progressArea = computed(() => {
  if (!samples.value.length) return ''
  const pts = samples.value.map((s, i) => `${x(times.value[i]).toFixed(1)},${(BOTTOM - (s.progress ?? 0) * (BOTTOM - TOP)).toFixed(1)}`)
  return `${x(t0.value)},${BOTTOM} ${pts.join(' ')} ${x(t1.value)},${BOTTOM}`
})

const MARKER_KINDS = new Set([
  'printer.failure.detected',
  'printer.pause.confirmed',
  'printer.pause.unconfirmed',
  'printer.print.started',
  'printer.print.finished',
  'printer.plate.uploaded',
])
const markers = computed(() =>
  live.feed
    .filter((e) => e.d.printerId === props.printerId && MARKER_KINDS.has(e.d.kind))
    .map((e) => ({ key: e.key, t: new Date(e.at).getTime(), tone: e.d.tone as Tone, text: e.d.text }))
    .filter((m) => m.t >= t0.value && m.t <= t1.value),
)

const at = computed(() => samples.value[cursor.value])
const cursorX = computed(() => (at.value ? x(times.value[cursor.value]) : W))

function onScrub() {
  following.value = cursor.value >= samples.value.length - 1
  stop()
}
function play() {
  if (player) return stop()
  if (cursor.value >= samples.value.length - 1) cursor.value = 0
  following.value = false
  player = setInterval(() => {
    if (cursor.value >= samples.value.length - 1) {
      stop()
      following.value = true
      return
    }
    cursor.value += 1
  }, 80)
}
function stop() {
  if (player) clearInterval(player)
  player = null
}
onBeforeUnmount(stop)
</script>

<template>
  <section class="panel replay" :aria-busy="loading">
    <div class="panel-head">
      <h2>Replay · {{ printerId }}</h2>
      <div class="controls">
        <label class="sr-only" for="replay-minutes">Window</label>
        <select id="replay-minutes" v-model.number="minutes" class="select">
          <option :value="15">last 15 min</option>
          <option :value="30">last 30 min</option>
          <option :value="60">last hour</option>
        </select>
        <button type="button" class="btn btn-sm" :aria-pressed="!!player" :disabled="samples.length < 2" @click="play">
          <Icon :name="player ? 'pause' : 'play'" :size="14" />{{ player ? 'Stop' : 'Play' }}
        </button>
        <button v-if="closable" type="button" class="icon-btn small" aria-label="Close the replay" @click="$emit('close')">
          <Icon name="x" :size="14" />
        </button>
      </div>
    </div>

    <p v-if="error" class="panel-note text-danger" role="alert">{{ error }}</p>
    <p v-else-if="!loading && !samples.length" class="empty">No telemetry from {{ printerId }} in this window (the stream keeps one hour).</p>

    <template v-if="samples.length">
      <svg class="chart" :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" role="img" :aria-label="`${printerId} temperatures and progress over the last ${minutes} minutes`">
        <polygon :points="progressArea" class="progress" />
        <polyline :points="nozzleTarget" class="target" />
        <polyline :points="bed" class="bed" />
        <polyline :points="nozzle" class="nozzle" />
        <line v-for="m in markers" :key="m.key" :x1="x(m.t)" :x2="x(m.t)" :y1="TOP" :y2="BOTTOM" :class="`marker tone-${m.tone}`">
          <title>{{ m.text }}</title>
        </line>
        <line :x1="cursorX" :x2="cursorX" :y1="0" :y2="H" class="cursor" />
      </svg>
      <div class="legend">
        <span><i class="sw nozzle" />Nozzle</span>
        <span><i class="sw target" />Target</span>
        <span><i class="sw bed" />Bed</span>
        <span><i class="sw progress" />Progress</span>
        <span class="muted">{{ markers.length }} event marker{{ markers.length === 1 ? '' : 's' }}</span>
      </div>
      <label class="sr-only" for="replay-scrub">Moment</label>
      <input id="replay-scrub" v-model.number="cursor" type="range" min="0" :max="samples.length - 1" class="scrub" @input="onScrub" />
      <div v-if="at" class="readout">
        <span class="mono">{{ clock(at.observed_at) }}</span>
        <StatusPill :tone="STATUS_TONE[at.status] ?? 'idle'" :label="title(at.status)" />
        <span>Nozzle <b class="mono">{{ temp(at.nozzle_temp) }}/{{ temp(at.nozzle_target) }}°</b></span>
        <span>Bed <b class="mono">{{ temp(at.bed_temp) }}/{{ temp(at.bed_target) }}°</b></span>
        <span>{{ pct(at.progress) }} · layer {{ at.layer ?? '—' }}/{{ at.total_layers ?? '—' }}</span>
        <span v-if="at.plate_id" class="chip">{{ at.plate_id }}</span>
        <span v-if="following" class="chip accent">live</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.controls {
  display: flex;
  gap: 8px;
  align-items: center;
}
.controls .select {
  min-height: 32px;
  font-size: 13px;
}
.icon-btn.small {
  width: 32px;
  height: 32px;
}
.chart {
  width: 100%;
  height: 200px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
polyline {
  fill: none;
  stroke-width: 1.6;
  vector-effect: non-scaling-stroke;
}
.nozzle {
  stroke: var(--accent);
}
.target {
  stroke: var(--muted);
  stroke-dasharray: 4 4;
}
.bed {
  stroke: var(--warn);
}
.progress {
  fill: var(--ok-bg);
  stroke: none;
}
.cursor {
  stroke: var(--text);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.marker {
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
  stroke: var(--idle);
}
.marker.tone-danger {
  stroke: var(--danger);
}
.marker.tone-ok {
  stroke: var(--ok);
}
.marker.tone-accent {
  stroke: var(--accent);
}
.marker.tone-warn {
  stroke: var(--warn);
}
.legend {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--muted);
}
.sw {
  display: inline-block;
  width: 14px;
  height: 3px;
  margin-inline-end: 6px;
  vertical-align: middle;
  border-radius: 2px;
}
.sw.nozzle {
  background: var(--accent);
}
.sw.target {
  background: var(--muted);
}
.sw.bed {
  background: var(--warn);
}
.sw.progress {
  background: var(--ok); opacity: 0.5;
  height: 8px;
}
.scrub {
  width: 100%;
  accent-color: var(--accent);
}
.readout {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
  font-size: 13px;
}
</style>
