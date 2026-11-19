<script setup lang="ts">
// Printer Fleet (design screen 3): every printer with its controls, status filters, batch actions on a selection,
// printer details (hardware, calibration, history) and the failure / event log.
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import FleetLog from '@/components/fleet/FleetLog.vue'
import PrinterDrawer from '@/components/fleet/PrinterDrawer.vue'
import PrinterTile from '@/components/live/PrinterTile.vue'
import Icon from '@/components/ui/Icon.vue'
import { command, errorText, printerCommand } from '@/lib/api'
import { BATCH_LABEL, type BatchAction, describePlan, planBatch } from '@/lib/fleet'
import { clock } from '@/lib/format'
import type { Printer } from '@/lib/types'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { useLive } from '@/stores/live'
import { type DisplayStatus, displayStatus, STATUS_TONE, usePrinters } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'
import { vCan } from '@/directives/can'

const route = useRoute()
const router = useRouter()
const printers = usePrinters()
const auth = useAuth()
const live = useLive()
const confirm = useConfirm()
const toasts = useToasts()

// ---------------------------------------------------------------- filters and selection
const ORDER: DisplayStatus[] = ['printing', 'heating', 'paused', 'calibrating', 'failed', 'finished', 'idle', 'offline']
const filter = ref<DisplayStatus | 'all'>('all')
const counts = computed(() => ORDER.filter((s) => printers.counts[s]).map((s) => ({ s, n: printers.counts[s], tone: STATUS_TONE[s] })))
const shown = computed(() => printers.list.filter((p) => filter.value === 'all' || displayStatus(p) === filter.value))
const selected = ref<string[]>([])
const selection = computed(() => printers.list.filter((p) => selected.value.includes(p.id)))
const allShownSelected = computed(() => shown.value.length > 0 && shown.value.every((p) => selected.value.includes(p.id)))

function toggle(id: string) {
  selected.value = selected.value.includes(id) ? selected.value.filter((x) => x !== id) : [...selected.value, id]
}
function selectAll() {
  selected.value = allShownSelected.value ? [] : shown.value.map((p) => p.id)
}

// ---------------------------------------------------------------- details drawer (?printer=<id>)
const open = computed(() => (typeof route.query.printer === 'string' && printers.get(route.query.printer) ? route.query.printer : null))
const openDetails = (id: string) => void router.replace({ query: { ...route.query, printer: id } })
const closeDetails = () => {
  const { printer: _drop, ...rest } = route.query
  void router.replace({ query: rest })
}

// ---------------------------------------------------------------- batch actions
const ACTIONS: BatchAction[] = ['pause', 'resume', 'cancel', 'clear_bed', 'home']
const plans = computed(() => Object.fromEntries(ACTIONS.map((a) => [a, planBatch(a, selection.value, displayStatus)])))
const running = ref(false)

function send(action: BatchAction, p: Printer): Promise<unknown> {
  switch (action) {
    case 'pause':
      return printerCommand(p.id, 'pause', { reason: 'batch pause from the fleet screen' })
    case 'resume':
      return printerCommand(p.id, 'resume')
    case 'cancel':
      return printerCommand(p.id, 'cancel', { reason: 'batch cancel from the fleet screen' })
    case 'clear_bed':
      return command('printers.clear_bed', { printer_id: p.id })
    case 'home':
      return command('printers.calibrate', { printer_id: p.id, routine: 'home', bed_clear_confirmed: true })
  }
}

const CONFIRM: Record<BatchAction, { text: string; danger?: boolean; check?: string }> = {
  pause: { text: 'Each pause is checked against the printer’s own state; any printer that does not stop raises an alert.' },
  resume: { text: 'The printers continue their plates.' },
  cancel: {
    text: 'The prints stop and their plates count as failed; the parts are queued again automatically (up to 2 attempts each).',
    danger: true,
  },
  clear_bed: { text: 'The scheduler then sends these printers their next plates.', check: 'I removed the finished plates; these beds are empty.' },
  home: { text: 'Every axis moves to its endstop.', check: 'These beds are empty and nothing blocks the print heads.' },
}

async function batch(action: BatchAction) {
  const plan = plans.value[action]
  if (!plan.eligible.length) return
  const c = CONFIRM[action]
  const ok = await confirm.ask({
    title: `${BATCH_LABEL[action]}: ${plan.eligible.length} printer${plan.eligible.length === 1 ? '' : 's'}?`,
    text: `${describePlan(plan)}. ${c.text}`,
    confirmLabel: BATCH_LABEL[action],
    danger: c.danger,
    requireCheck: c.check,
  })
  if (!ok) return
  running.value = true
  const results = await Promise.allSettled(plan.eligible.map((p) => send(action, p)))
  running.value = false
  const failed = results.flatMap((r, i) => (r.status === 'rejected' ? [`${plan.eligible[i].id}: ${errorText(r.reason)}`] : []))
  const sent = results.length - failed.length
  if (failed.length) toasts.error(`${BATCH_LABEL[action]}: ${sent} sent, ${failed.length} failed`, failed.join('; '))
  else toasts.info(`${BATCH_LABEL[action]}: sent to ${sent} printer${sent === 1 ? '' : 's'}`, 'Each printer reports its result in the event log.')
  selected.value = []
}

</script>

<template>
  <div class="page-head">
    <div>
      <h1>Printer Fleet</h1>
      <p>{{ printers.list.length }} printers · telemetry via MQTT / Moonraker · cameras via go2rtc → MJPEG</p>
    </div>
    <div class="head-actions">
      <RouterLink v-if="auth.can('operator')" to="/fleet/add" class="btn btn-primary" data-testid="add-printer"><Icon name="plus" :size="14" />Add printer</RouterLink>
    </div>
  </div>


  <div v-for="a in live.alerts.slice(0, 3)" :key="a.key" class="banner" role="alert">
    <Icon name="alert" :size="18" />
    <div class="banner-text">
      <b>{{ a.d.text }}</b>
      <span class="muted">{{ clock(a.at) }} · {{ a.item.topic }}</span>
    </div>
    <div class="banner-actions">
      <button v-if="a.d.printerId" type="button" class="btn btn-sm" @click="openDetails(a.d.printerId)">Details</button>
      <button type="button" class="btn btn-sm" @click="live.acknowledge(a.key)"><Icon name="check" :size="14" />Acknowledge</button>
    </div>
  </div>

  <div class="toolbar">
    <div class="chips" role="group" aria-label="Filter by state" data-tour="fleet-filters">
      <button type="button" class="chip-btn" :class="{ on: filter === 'all' }" :aria-pressed="filter === 'all'" @click="filter = 'all'">
        All {{ printers.list.length }}
      </button>
      <button
        v-for="c in counts"
        :key="c.s"
        type="button"
        class="chip-btn"
        :class="[{ on: filter === c.s }, `tone-${c.tone}`]"
        :aria-pressed="filter === c.s"
        @click="filter = filter === c.s ? 'all' : c.s"
      >
        <span class="dot" aria-hidden="true" />{{ c.n }} {{ c.s }}
      </button>
    </div>
    <button type="button" class="btn btn-sm" :disabled="!shown.length" @click="selectAll">
      {{ allShownSelected ? 'Clear selection' : 'Select all' }}
    </button>
  </div>

  <div v-if="selection.length" class="batch" role="region" aria-label="Batch actions" data-testid="batch-bar">
    <span class="count"><b>{{ selection.length }}</b> selected</span>
    <div class="batch-actions">
      <button v-can="'operator'"
        v-for="a in ACTIONS"
        :key="a"
        type="button"
        class="btn btn-sm"
        :class="{ 'btn-danger': a === 'pause' || a === 'cancel' }"
        :disabled="running || !plans[a].eligible.length"
        :title="plans[a].skipped.map((s) => `${s.printer.id}: ${s.reason}`).join('\n')"
        :data-testid="`batch-${a}`"
        @click="batch(a)"
      >
        {{ BATCH_LABEL[a] }}<span class="n">{{ plans[a].eligible.length }}</span>
      </button>
    </div>
    <button type="button" class="btn btn-sm" @click="selected = []">Clear</button>
  </div>

  <p v-if="printers.error" class="banner" role="alert"><Icon name="alert" :size="18" /><span class="banner-text">{{ printers.error }}</span></p>

  <section class="tiles" aria-label="Printers">
    <PrinterTile
      v-for="p in shown"
      :key="p.id"
      :printer="p"
      :history="false"
      details
      selectable
      :checked="selected.includes(p.id)"
      :selected="p.id === open"
      @toggle="toggle"
      @details="openDetails"
    />
  </section>
  <p v-if="!shown.length" class="panel empty">
    {{ printers.list.length ? 'No printer in this state.' : 'No printers yet: see Add printer.' }}
  </p>

  <FleetLog @open="openDetails" />

  <PrinterDrawer v-if="open" :printer-id="open" @close="closeDetails" />
</template>

<style scoped>
.banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--danger);
  background: var(--danger-bg);
  color: var(--danger);
  flex-wrap: wrap;
}
.banner-text {
  flex: 1 1 260px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--text);
  font-size: 13px;
}
.banner-actions {
  display: flex;
  gap: 8px;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.chip-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text);
  font: 500 12px var(--font);
  cursor: pointer;
}
.chip-btn.on {
  border-color: var(--accent);
  background: var(--accent-bg);
}
.batch {
  position: sticky;
  top: 72px;
  z-index: 15;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--accent);
  background: var(--surface);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
}
.batch-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  flex: 1;
}
.n {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--bg);
  font: 600 11px var(--font-mono);
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: 16px;
}
</style>
