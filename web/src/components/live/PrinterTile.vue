<script setup lang="ts">
// One printer: camera, state, temperatures, progress and its controls. Used by the Live floor (History) and the
// Printer Fleet screen (selection for batch actions, Details).
import { computed, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { command, errorText, printerCommand } from '@/lib/api'
import { duration, pct, temp, title } from '@/lib/format'
import type { Printer } from '@/lib/types'
import { vCan } from '@/directives/can'
import { useConfirm } from '@/stores/confirm'
import { displayStatus, STATUS_TONE } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'

import CameraView from './CameraView.vue'

const props = withDefaults(
  defineProps<{ printer: Printer; selected?: boolean; selectable?: boolean; checked?: boolean; history?: boolean; details?: boolean }>(),
  { selected: false, selectable: false, checked: false, history: true, details: false },
)
defineEmits<{ history: [id: string]; details: [id: string]; toggle: [id: string] }>()

const toasts = useToasts()
const confirm = useConfirm()
const busy = ref(false)
const status = computed(() => displayStatus(props.printer))
const s = computed(() => props.printer.state)
const progress = computed(() => Math.round((s.value?.progress ?? 0) * 100))
const running = computed(() => ['printing', 'heating', 'paused'].includes(status.value))
const needsClearing = computed(() => props.printer.online && !props.printer.bed_clear && ['idle', 'finished', 'failed'].includes(status.value))
const sub = computed(() => {
  if (status.value === 'calibrating') return `Calibrating · ${s.value?.hardware?.stage ?? props.printer.calibration?.routine.replaceAll('_', ' ') ?? 'routine'}`
  if (s.value?.plate_id) return s.value.plate_id
  if (needsClearing.value) return 'Plate done: clear the bed'
  return props.printer.online ? 'Ready for the next plate' : 'Not connected'
})

async function run(label: string, fn: () => Promise<unknown>, note?: string) {
  busy.value = true
  try {
    await fn()
    toasts.info(label, note)
  } catch (err) {
    toasts.error(`${label} failed`, errorText(err))
  } finally {
    busy.value = false
  }
}

const id = computed(() => props.printer.id)
const pause = () => run(`Pausing ${id.value}`, () => printerCommand(id.value, 'pause'), 'The pause is checked against the printer’s own state.')
const resume = () => run(`Resuming ${id.value}`, () => printerCommand(id.value, 'resume'))

async function cancel() {
  const ok = await confirm.ask({
    title: `Cancel the print on ${id.value}?`,
    text: 'The printer stops and the plate counts as failed. Its parts go back into the queue and are printed again automatically (each part gets up to 2 attempts).',
    confirmLabel: 'Cancel print',
    danger: true,
  })
  if (ok) await run(`Cancelling ${id.value}`, () => printerCommand(id.value, 'cancel', { reason: 'cancelled from the dashboard' }))
}

async function bedCleared() {
  const ok = await confirm.ask({
    title: `Bed of ${id.value} cleared?`,
    text: 'The scheduler then sends this printer its next plate.',
    confirmLabel: 'Bed is clear',
    requireCheck: 'I removed the finished plate and the bed is empty.',
  })
  if (ok) await run(`${id.value} ready for the next plate`, () => command('printers.clear_bed', { printer_id: id.value }))
}
</script>

<template>
  <article class="tile" :class="{ selected, checked }" :data-testid="`printer-${printer.id}`" data-tour="printer-tile">
    <CameraView :printer-id="printer.id" :available="printer.camera" :protocol="printer.protocol" :simulated="printer.simulated" />
    <label v-if="selectable" class="select" data-tour="tile-select" :title="`Select ${printer.id}`">
      <input type="checkbox" :checked="checked" :aria-label="`Select ${printer.id}`" @change="$emit('toggle', printer.id)" />
    </label>
    <div class="body">
      <div class="row">
        <span class="name">{{ printer.id }}</span>
        <StatusPill :tone="STATUS_TONE[status]" :label="title(status)" />
      </div>
      <span class="sub" :class="{ warn: needsClearing }">
        {{ sub }} · {{ printer.material }} {{ printer.color }}{{ printer.model ? ` · ${printer.model.toUpperCase()}` : '' }}
      </span>
      <div class="temps" data-tour="tile-temps">
        <div>
          <span class="label">Nozzle</span>
          <span class="value">{{ temp(s?.nozzle_temp) }}<span class="unit"> / {{ temp(s?.nozzle_target) }} °C</span></span>
        </div>
        <div>
          <span class="label">Bed</span>
          <span class="value">{{ temp(s?.bed_temp) }}<span class="unit"> / {{ temp(s?.bed_target) }} °C</span></span>
        </div>
      </div>
      <div class="progress" data-tour="tile-progress">
        <div class="meta">
          <span>{{ pct(s?.progress) }} · layer {{ s?.layer ?? '—' }} / {{ s?.total_layers ?? '—' }}</span>
          <span>ETA {{ status === 'printing' ? duration(s?.remaining_s) : '—' }}</span>
        </div>
        <div class="bar" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100" :aria-label="`${printer.id} progress`">
          <div :style="{ width: `${progress}%` }" :class="`fill-${STATUS_TONE[status]}`" />
        </div>
      </div>
      <div class="actions" data-tour="tile-actions">
        <button v-if="status === 'printing' || status === 'heating'" type="button" class="btn btn-sm btn-danger" :disabled="busy" :aria-label="`Pause ${printer.id}`" data-tour="tile-pause" v-can="'operator'" @click="pause">
          <Icon name="pause" :size="14" />Pause
        </button>
        <button v-if="status === 'paused'" type="button" class="btn btn-sm" :disabled="busy" :aria-label="`Resume ${printer.id}`" data-tour="tile-resume" v-can="'operator'" @click="resume">
          <Icon name="play" :size="14" />Resume
        </button>
        <button v-if="running && details" type="button" class="btn btn-sm" :disabled="busy" :aria-label="`Cancel the print on ${printer.id}`" v-can="'operator'" @click="cancel">
          <Icon name="stop" :size="14" />Cancel
        </button>
        <button v-if="needsClearing" type="button" class="btn btn-sm" :disabled="busy" :aria-label="`Bed of ${printer.id} cleared`" data-tour="bed-cleared" v-can="'operator'" @click="bedCleared">
          <Icon name="check" :size="14" />Bed cleared
        </button>
        <button v-if="history" type="button" class="btn btn-sm" :aria-pressed="selected" data-tour="tile-history" @click="$emit('history', printer.id)">
          <Icon name="history" :size="14" />History
        </button>
        <button v-if="details" type="button" class="btn btn-sm" :aria-label="`Details of ${printer.id}`" data-tour="tile-details" @click="$emit('details', printer.id)">
          Details
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.tile {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.tile.selected,
.tile.checked {
  border-color: var(--accent);
}
.select {
  position: absolute;
  left: 10px;
  top: 124px;
  display: inline-flex;
  padding: 6px;
  border-radius: 6px;
  background: var(--bg);
  border: 1px solid var(--border);
  cursor: pointer;
}
.select input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--accent);
  cursor: pointer;
}
.body {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.name {
  font: 600 15px var(--font);
}
.sub {
  font-size: 12px;
  color: var(--muted);
}
.sub.warn {
  color: var(--warn);
}
.temps {
  display: flex;
  gap: 12px;
}
.temps > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}
.label {
  font: 500 11px var(--font);
  color: var(--muted);
}
.value {
  font: 600 16px var(--font-mono);
}
.unit {
  font-size: 12px;
  color: var(--muted);
}
.progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.meta {
  display: flex;
  justify-content: space-between;
  font: 500 12px var(--font-mono);
  color: var(--muted);
}
.bar {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}
.bar > div {
  height: 100%;
  border-radius: 999px;
  transition: width 0.4s ease;
}
.fill-ok {
  background: var(--ok);
}
.fill-warn {
  background: var(--warn);
}
.fill-danger {
  background: var(--danger);
}
.fill-accent {
  background: var(--accent);
}
.fill-idle {
  background: var(--idle);
}
.actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  flex-wrap: wrap;
  min-height: 32px;
}
</style>
