<script setup lang="ts">
// Calibration utilities for one printer: the routines it supports, a guarded Run (the operator confirms the bed is
// empty), the running one, and recent runs. The telemetry agent re-checks everything and reports each outcome.
import { computed, reactive, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { command, errorText } from '@/lib/api'
import { clock, dateTime, duration } from '@/lib/format'
import type { CalibrationRun, PrinterDetail, Routine, Tone } from '@/lib/types'
import { useConfirm } from '@/stores/confirm'
import { displayStatus, usePrinters } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ detail: PrinterDetail }>()
const emit = defineEmits<{ requested: [] }>()

const printers = usePrinters()
const confirm = useConfirm()
const toasts = useToasts()
const busy = ref<string | null>(null)
const temps = reactive<Record<string, number>>({})

const printer = computed(() => printers.get(props.detail.printer.id) ?? props.detail.printer)
const status = computed(() => displayStatus(printer.value))
const running = computed(() => printer.value.calibration ?? null)
const blocked = computed(() => {
  if (!printer.value.online) return 'The printer is offline.'
  if (running.value || status.value === 'calibrating') return 'A calibration is running.'
  if (!['idle', 'finished', 'failed'].includes(status.value)) return `The printer is ${status.value}: calibrate when it is idle.`
  return ''
})
const label = (key: string) => props.detail.routines.find((r) => r.key === key)?.label ?? key.replaceAll('_', ' ')

const STATUS: Record<CalibrationRun['status'], { tone: Tone; label: string }> = {
  running: { tone: 'accent', label: 'Running' },
  done: { tone: 'ok', label: 'Done' },
  failed: { tone: 'danger', label: 'Failed' },
  refused: { tone: 'idle', label: 'Not started' },
}

function took(run: CalibrationRun): string {
  if (!run.finished_at) return '—'
  return duration((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)
}

async function start(r: Routine) {
  const t = r.heater ? (temps[r.key] ?? r.default_temp ?? undefined) : undefined
  const ok = await confirm.ask({
    title: `${r.label} on ${printer.value.id}?`,
    text: `${r.help} The printer moves and heats on its own for up to ${r.max_minutes} minutes${t ? ` (target ${t} °C)` : ''}. Stay near it; Pause all printers does not stop a calibration, the printer’s own screen does.`,
    confirmLabel: `Start ${r.label.toLowerCase()}`,
    requireCheck: 'The bed is empty and nothing blocks the print head.',
  })
  if (!ok) return
  busy.value = r.key
  try {
    await command('printers.calibrate', {
      printer_id: printer.value.id,
      routine: r.key,
      bed_clear_confirmed: true,
      ...(t ? { temperature: t } : {}),
    })
    toasts.info(`${r.label} requested`, 'The printer reports here when it starts and finishes.')
    emit('requested')
  } catch (err) {
    toasts.error(`${r.label} not requested`, errorText(err))
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="calibration">
    <p class="intro" data-tour="calibration-intro">
      Routines run only on an online, idle printer, one at a time; the scheduler sends it no plate meanwhile. Every
      run is checked against the printer's own state and recorded below.
    </p>

    <div v-if="running" class="running" role="status">
      <Icon name="refresh" :size="16" />
      <span><b>{{ label(running.routine) }}</b> running since {{ clock(running.started_at) }} · requested by {{ running.requested_by }}</span>
    </div>
    <p v-else-if="blocked" class="blocked">{{ blocked }}</p>

    <ul class="routines" data-tour="calibration-routines">
      <li v-for="r in detail.routines" :key="r.key" :data-testid="`routine-${r.key}`">
        <div class="text">
          <span class="name">{{ r.label }}</span>
          <span class="help">{{ r.help }} Up to {{ r.max_minutes }} min.</span>
        </div>
        <label v-if="r.heater" class="temp">
          <span class="sr-only">{{ r.label }} target temperature</span>
          <input
            type="number"
            class="input"
            :min="r.heater === 'bed' ? 40 : 150"
            :max="r.heater === 'bed' ? 110 : 300"
            :value="temps[r.key] ?? r.default_temp ?? undefined"
            @input="temps[r.key] = Number(($event.target as HTMLInputElement).value)"
          />
          °C
        </label>
        <button type="button" class="btn btn-sm" :disabled="!!blocked || busy !== null" @click="start(r)">
          <Icon name="play" :size="14" />Run
        </button>
      </li>
    </ul>
    <p v-if="!detail.routines.length" class="empty">No calibration routines for this printer type.</p>

    <h3>Recent runs</h3>
    <div class="table-wrap">
      <table class="data runs">
        <thead>
          <tr>
            <th scope="col">Started</th>
            <th scope="col">Routine</th>
            <th scope="col">By</th>
            <th scope="col">Result</th>
            <th scope="col">Took</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in detail.calibrations" :key="run.calibration_id">
            <td class="num">{{ dateTime(run.started_at) }}</td>
            <td>{{ label(run.routine) }}<span v-if="run.temperature" class="muted"> · {{ run.temperature }} °C</span></td>
            <td>{{ run.requested_by }}</td>
            <td><StatusPill :tone="STATUS[run.status].tone" :label="STATUS[run.status].label" /></td>
            <td class="num">{{ took(run) }}</td>
            <td class="detail">{{ run.detail ?? '' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!detail.calibrations.length" class="empty">No calibration has run on this printer yet.</p>
    </div>
  </div>
</template>

<style scoped>
.calibration {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.intro {
  font-size: 13px;
  color: var(--muted);
}
.running {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--accent);
  background: var(--accent-bg);
  font-size: 13px;
}
.blocked {
  font-size: 13px;
  color: var(--warn);
}
.routines {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.routines li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.name {
  font-weight: 500;
}
.help {
  font-size: 12px;
  color: var(--muted);
}
.temp {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.temp .input {
  width: 72px;
  min-height: 32px;
  font-size: 13px;
}
h3 {
  font: 600 13px var(--font);
  margin-top: 4px;
}
.runs {
  min-width: 0;
}
.runs td {
  white-space: nowrap;
}
.runs td.detail {
  white-space: normal;
  font-size: 12px;
  color: var(--muted);
}
</style>
