<script setup lang="ts">
import { computed, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { errorText, printerCommand } from '@/lib/api'
import { duration, pct, temp, title } from '@/lib/format'
import type { Printer } from '@/lib/types'
import { displayStatus, STATUS_TONE } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'

import CameraView from './CameraView.vue'

const props = defineProps<{ printer: Printer; selected?: boolean }>()
defineEmits<{ history: [id: string] }>()

const toasts = useToasts()
const busy = ref(false)
const status = computed(() => displayStatus(props.printer))
const s = computed(() => props.printer.state)
const progress = computed(() => Math.round((s.value?.progress ?? 0) * 100))

async function act(action: 'pause' | 'resume') {
  busy.value = true
  try {
    await printerCommand(props.printer.id, action)
    toasts.info(
      action === 'pause' ? `Pausing ${props.printer.id}` : `Resuming ${props.printer.id}`,
      action === 'pause' ? 'The pause is checked against the printer’s own state.' : undefined,
    )
  } catch (err) {
    toasts.error(`${title(action)} failed`, errorText(err))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <article class="tile" :class="{ selected }" :data-testid="`printer-${printer.id}`">
    <CameraView :printer-id="printer.id" :available="printer.camera" :protocol="printer.protocol" :simulated="printer.simulated" />
    <div class="body">
      <div class="row">
        <span class="name">{{ printer.id }}</span>
        <StatusPill :tone="STATUS_TONE[status]" :label="title(status)" />
      </div>
      <span class="sub">
        {{ s?.plate_id ?? (printer.online ? 'Ready for the next plate' : 'Not connected') }}
        · {{ printer.material }} {{ printer.color }}{{ printer.model ? ` · ${printer.model.toUpperCase()}` : '' }}
      </span>
      <div class="temps">
        <div>
          <span class="label">Nozzle</span>
          <span class="value">{{ temp(s?.nozzle_temp) }}<span class="unit"> / {{ temp(s?.nozzle_target) }} °C</span></span>
        </div>
        <div>
          <span class="label">Bed</span>
          <span class="value">{{ temp(s?.bed_temp) }}<span class="unit"> / {{ temp(s?.bed_target) }} °C</span></span>
        </div>
      </div>
      <div class="progress">
        <div class="meta">
          <span>{{ pct(s?.progress) }} · layer {{ s?.layer ?? '—' }} / {{ s?.total_layers ?? '—' }}</span>
          <span>ETA {{ status === 'printing' ? duration(s?.remaining_s) : '—' }}</span>
        </div>
        <div class="bar" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100" :aria-label="`${printer.id} progress`">
          <div :style="{ width: `${progress}%` }" :class="`fill-${STATUS_TONE[status]}`" />
        </div>
      </div>
      <div class="actions">
        <button v-if="status === 'printing' || status === 'heating'" type="button" class="btn btn-sm btn-danger" :disabled="busy" :aria-label="`Pause ${printer.id}`" @click="act('pause')">
          <Icon name="pause" :size="14" />Pause
        </button>
        <button v-if="status === 'paused'" type="button" class="btn btn-sm" :disabled="busy" :aria-label="`Resume ${printer.id}`" @click="act('resume')">
          <Icon name="play" :size="14" />Resume
        </button>
        <button type="button" class="btn btn-sm" :aria-pressed="selected" @click="$emit('history', printer.id)">
          <Icon name="history" :size="14" />History
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.tile {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.tile.selected {
  border-color: var(--accent);
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
  min-height: 32px;
}
</style>
