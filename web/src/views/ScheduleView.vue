<script setup lang="ts">
// Schedule (Wave 2 / W2-C): what each printer prints over the next hours and why (the fleet agent's own policy:
// your order, due dates, priority, the overnight window, quiet hours, pins and holds), the filament no printer has
// loaded, and the queue with hold / pin / move. Words come from the i18n catalogue (src/i18n/en.ts).
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import { vCan } from '@/directives/can'
import { errorText } from '@/lib/api'
import { dateTime, duration, uiLocale } from '@/lib/format'
import { type Block, hourLabel, type JobAction, type Lane, placeBlock, type QueuedJob, useSchedule } from '@/stores/schedule'
import { useToasts } from '@/stores/toasts'

const { t } = useI18n()
const schedule = useSchedule()
const toasts = useToasts()
onMounted(() => void schedule.load())

const s = computed(() => schedule.data)
const asTable = ref(false)

const time = (iso: string) => new Date(iso).toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit', hour12: false })

// ---------------------------------------------------------------- the time axis and the day's windows
const ticks = computed(() => {
  if (!s.value) return []
  const t0 = new Date(s.value.now).getTime()
  const span = s.value.horizon_h * 3600_000
  const first = new Date(t0)
  first.setMinutes(0, 0, 0)
  first.setHours(first.getHours() + (3 - (first.getHours() % 3)))
  const out: { left: number; label: string }[] = []
  for (let x = first.getTime(); x < t0 + span; x += 3 * 3600_000) {
    if (x - t0 < span * 0.04) continue          // too close to "now" to label
    out.push({ left: ((x - t0) / span) * 100, label: time(new Date(x).toISOString()) })
  }
  return out
})

function at(day: Date, h: number): number {
  const d = new Date(day)
  d.setHours(Math.floor(h), Math.round((h % 1) * 60), 0, 0)
  return d.getTime()
}

/** Shaded spans: the overnight window (long jobs first) and quiet hours (no new starts). */
const bands = computed(() => {
  if (!s.value) return []
  const w = s.value.window
  const t0 = new Date(s.value.now).getTime()
  const span = s.value.horizon_h * 3600_000
  const out: { kind: 'night' | 'quiet'; left: number; width: number }[] = []
  const add = (kind: 'night' | 'quiet', a: number, b: number) => {
    const l = Math.max(0, a - t0)
    const r = Math.min(span, b - t0)
    if (r > l) out.push({ kind, left: (l / span) * 100, width: ((r - l) / span) * 100 })
  }
  for (let d = -1; d <= 1; d++) {
    const day = new Date(t0 + d * 86400_000)
    const next = new Date(t0 + (d + 1) * 86400_000)
    const nightEnd = w.quiet_enabled ? (w.quiet_from > w.overnight_from ? at(day, w.quiet_from) : at(next, w.quiet_from)) : at(next, w.quiet_until)
    add('night', at(day, w.overnight_from), nightEnd)
    if (w.quiet_enabled) add('quiet', at(day, w.quiet_from), w.quiet_from > w.quiet_until ? at(next, w.quiet_until) : at(day, w.quiet_until))
  }
  return out
})

function blocksOf(lane: Lane) {
  if (!s.value) return []
  return lane.blocks
    .map((b) => ({ b, pos: placeBlock(b, s.value!.now, s.value!.horizon_h) }))
    .filter((x): x is { b: Block; pos: { left: number; width: number } } => x.pos !== null)
}

const blockLabel = (b: Block) => (b.jobs.length === 1 ? (b.jobs[0].order_id ?? b.jobs[0].job_id) : t('schedule.plates', { n: b.jobs.length }))
const blockState = (b: Block) => (b.running ? (b.note === 'printing' ? t('schedule.running') : t('schedule.scheduled')) : t('schedule.planned'))

// Hover / focus card for a block.
const tip = ref<{ lane: string; b: Block; left: number } | null>(null)
function show(lane: Lane, b: Block, left: number) {
  tip.value = { lane: lane.printer_id, b, left: Math.min(Math.max(left, 8), 70) }
}

// ---------------------------------------------------------------- the queue
const printers = computed(() => s.value?.lanes ?? [])
const pinnable = (j: QueuedJob) => printers.value.filter((l) => l.material === j.material && l.color === j.color)

async function act(j: QueuedJob, action: JobAction, printerId?: string) {
  try {
    await schedule.control(j.job_id, action, printerId)
    toasts.info(t('schedule.sent'), j.job_id)
    schedule.reloadSoon(900)
  } catch (err) {
    toasts.error(j.job_id, errorText(err))
  }
}

function onPin(j: QueuedJob, e: Event) {
  const v = (e.target as HTMLSelectElement).value
  void (v ? act(j, 'pin', v) : act(j, 'unpin'))
}

const settingsLink = '/system'
</script>

<template>
  <div class="page-head">
    <div>
      <h1>{{ t('schedule.title') }}</h1>
      <p>{{ t('schedule.subtitle', { h: s?.horizon_h ?? 24 }) }}</p>
    </div>
  </div>
  <p v-if="schedule.error" class="err" role="alert">{{ schedule.error }}</p>

  <div v-if="s" class="windows small" data-tour="schedule-window" data-testid="schedule-window">
    <span class="wchip night"><Icon name="clock" :size="13" />{{ t('schedule.overnight', { from: hourLabel(s.window.overnight_from) }) }}</span>
    <span v-if="s.window.quiet_now" class="wchip quiet" data-testid="quiet-now">{{ t('schedule.quietNow', { until: hourLabel(s.window.quiet_until) }) }}</span>
    <span v-else-if="s.window.quiet_enabled" class="wchip quiet">{{ t('schedule.quietOn', { from: hourLabel(s.window.quiet_from), until: hourLabel(s.window.quiet_until) }) }}</span>
    <span v-else class="wchip">{{ t('schedule.quietOff') }}</span>
    <RouterLink class="muted" :to="settingsLink">{{ t('schedule.settings') }}</RouterLink>
  </div>

  <section v-if="s" class="panel" data-tour="schedule-timeline">
    <div class="panel-head">
      <h2>{{ t('schedule.lanes') }}</h2>
      <button type="button" class="btn btn-sm" data-testid="timeline-toggle" @click="asTable = !asTable">{{ asTable ? t('schedule.timelineView') : t('schedule.tableView') }}</button>
    </div>

    <div v-if="!asTable" class="timeline" data-testid="timeline" @mouseleave="tip = null">
      <div class="axis" aria-hidden="true">
        <span class="lane-head" />
        <div class="track">
          <span class="tick now">{{ t('schedule.now') }}</span>
          <span v-for="k in ticks" :key="k.left" class="tick" :style="{ left: `${k.left}%` }">{{ k.label }}</span>
        </div>
      </div>
      <div v-for="lane in s.lanes" :key="lane.printer_id" class="lane" :data-testid="`lane-${lane.printer_id}`">
        <div class="lane-head">
          <b>{{ lane.printer_id }}</b>
          <span class="muted small">{{ lane.material }} {{ lane.color }}</span>
          <span v-if="lane.why_not" class="why small" :class="{ soft: lane.why_not === 'bed' }" :data-testid="`why-not-${lane.printer_id}`">{{ t(`schedule.unavailable.${lane.why_not}`) }}</span>
          <span v-if="lane.auto_eject" class="muted small">{{ t('schedule.platesLeft', { n: lane.plates_left }) }}</span>
        </div>
        <div class="track" role="list" :aria-label="lane.printer_id">
          <span v-for="(band, i) in bands" :key="i" class="band" :class="band.kind" :style="{ left: `${band.left}%`, width: `${band.width}%` }" aria-hidden="true" />
          <span v-for="g in ticks" :key="g.left" class="grid" :style="{ left: `${g.left}%` }" aria-hidden="true" />
          <button v-for="({ b, pos }, i) in blocksOf(lane)" :key="i" type="button" role="listitem" class="block"
            :class="{ running: b.running }" :style="{ left: `${pos.left}%`, width: `${pos.width}%` }"
            :aria-label="`${blockState(b)} ${time(b.start)}–${time(b.end)}: ${b.jobs.map((j) => j.job_id).join(', ')}`"
            :data-testid="`block-${lane.printer_id}-${i}`"
            @mouseenter="show(lane, b, pos.left)" @focus="show(lane, b, pos.left)" @blur="tip = null">
            <span v-if="pos.width > 6">{{ blockLabel(b) }}</span>
          </button>
          <span v-if="!lane.blocks.length" class="muted small nothing">{{ t('schedule.nothing') }}</span>
          <div v-if="tip && tip.lane === lane.printer_id" class="tip" role="tooltip" :style="{ left: `${tip.left}%` }">
            <b>{{ blockState(tip.b) }} · {{ time(tip.b.start) }}–{{ time(tip.b.end) }}</b>
            <span v-if="tip.b.note && !tip.b.running" class="muted">{{ tip.b.note }}</span>
            <span v-for="j in tip.b.jobs" :key="j.job_id">{{ j.order_id ?? j.job_id }} <span class="muted">· {{ j.reason || j.job_id }}</span></span>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="table-wrap">
      <table class="data compact" data-testid="timeline-table">
        <thead><tr><th>{{ t('schedule.lane.printer') }}</th><th>{{ t('schedule.lane.state') }}</th><th>{{ t('schedule.lane.start') }}</th><th>{{ t('schedule.lane.end') }}</th><th>{{ t('schedule.lane.jobs') }}</th></tr></thead>
        <tbody>
          <template v-for="lane in s.lanes" :key="lane.printer_id">
            <tr v-for="(b, i) in lane.blocks" :key="`${lane.printer_id}-${i}`">
              <td>{{ lane.printer_id }}</td><td>{{ blockState(b) }}</td><td>{{ dateTime(b.start) }}</td><td>{{ dateTime(b.end) }}</td>
              <td>{{ b.jobs.map((j) => `${j.order_id ?? j.job_id}${j.reason ? ` (${j.reason})` : ''}`).join(', ') }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </section>

  <section v-if="s?.unplaced.length" class="panel spool" data-testid="unplaced" data-tour="schedule-spool">
    <div class="panel-head"><h2>{{ t('schedule.spool') }}</h2></div>
    <ul>
      <li v-for="u in s.unplaced" :key="`${u.material}-${u.color}`" :data-testid="`unplaced-${u.material}-${u.color}`">
        <Icon name="disc" :size="14" />{{ t('schedule.spoolLine', { material: u.material, color: u.color, n: u.jobs.length, h: (u.minutes / 60).toFixed(1) }) }}
      </li>
    </ul>
    <p class="muted small">{{ t('schedule.spoolHelp') }}</p>
  </section>

  <section v-if="s" class="panel" data-tour="schedule-queue">
    <div class="panel-head"><h2>{{ t('schedule.queue') }}</h2><span class="panel-note">{{ t('schedule.queueNote') }}</span></div>
    <div v-if="s.queue.length" class="table-wrap">
      <table class="data compact queue" data-testid="queue">
        <thead>
          <tr>
            <th>{{ t('schedule.col.job') }}</th><th>{{ t('schedule.col.filament') }}</th><th>{{ t('schedule.col.time') }}</th>
            <th>{{ t('schedule.col.due') }}</th><th>{{ t('schedule.col.planned') }}</th><th>{{ t('schedule.col.why') }}</th>
            <th>{{ t('schedule.col.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in s.queue" :key="j.job_id" :class="{ held: j.hold }" :data-testid="`queue-row-${j.job_id}`">
            <td>
              <b>{{ j.order_id }}</b><span class="muted small mono jid">{{ j.job_id }}</span>
              <span v-if="j.hold" class="tag">{{ t('schedule.held') }}</span>
              <span v-if="j.rank" class="tag accent">{{ t('schedule.ranked', { n: j.rank }) }}</span>
              <span v-if="j.pinned_printer" class="tag">{{ t('schedule.pinned', { p: j.pinned_printer }) }}</span>
            </td>
            <td>{{ j.material }} {{ j.color }}</td>
            <td>{{ duration(j.est_minutes * 60) }}</td>
            <td>{{ j.due_by ? dateTime(j.due_by) : '—' }}</td>
            <td :data-testid="`planned-${j.job_id}`">{{ j.planned_printer ? `${j.planned_printer} · ${dateTime(j.planned_start)}` : t('schedule.notPlanned', { h: s.horizon_h }) }}</td>
            <td class="small" :data-testid="`reason-${j.job_id}`">{{ j.reason }}</td>
            <td class="actions">
              <button v-can="'operator'" type="button" class="btn btn-sm" :data-testid="`hold-${j.job_id}`" @click="act(j, j.hold ? 'release' : 'hold')">{{ j.hold ? t('schedule.release') : t('schedule.hold') }}</button>
              <button v-can="'operator'" type="button" class="icon-btn" :title="t('schedule.top')" :aria-label="t('schedule.top')" :data-testid="`top-${j.job_id}`" @click="act(j, 'top')">⤒</button>
              <button v-can="'operator'" type="button" class="icon-btn" :title="t('schedule.up')" :aria-label="t('schedule.up')" :data-testid="`up-${j.job_id}`" @click="act(j, 'up')">↑</button>
              <button v-can="'operator'" type="button" class="icon-btn" :title="t('schedule.down')" :aria-label="t('schedule.down')" :data-testid="`down-${j.job_id}`" @click="act(j, 'down')">↓</button>
              <button v-if="j.rank" v-can="'operator'" type="button" class="btn btn-sm" :data-testid="`auto-${j.job_id}`" @click="act(j, 'auto')">{{ t('schedule.auto') }}</button>
              <select v-if="j.pinned_printer || pinnable(j).length" v-can="'operator'" class="select pin" :value="j.pinned_printer ?? ''" :aria-label="t('schedule.pin')" :data-testid="`pin-${j.job_id}`" @change="onPin(j, $event)">
                <option value="">{{ t('schedule.anyPrinter') }}</option>
                <option v-for="p in pinnable(j)" :key="p.printer_id" :value="p.printer_id">{{ t('schedule.pin') }} {{ p.printer_id }}</option>
              </select>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-else icon="check" :title="t('schedule.empty')" data-testid="queue-empty" />
  </section>
</template>

<style scoped>
.windows {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 0 0 var(--gap);
}
.wchip {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 10px;
  color: var(--text);
}
.wchip.quiet {
  border-color: var(--warn);
}
.panel {
  margin-bottom: var(--gap);
}
.timeline {
  display: flex;
  flex-direction: column;
  gap: 6px;
  position: relative;
}
.axis,
.lane {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
}
.axis .track {
  height: 18px;
  background: none;
  border: 0;
}
.lane-head {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.why {
  color: var(--danger-text);
}
.why.soft {
  color: var(--warn);
}
.track {
  position: relative;
  height: 40px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.tick {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}
.tick.now {
  inset-inline-start: 0;
  transform: none;
  color: var(--text);
  font-weight: 600;
}
.grid {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--border);
}
.band {
  position: absolute;
  top: 0;
  bottom: 0;
}
.band.night {
  background: var(--accent-bg);
}
.band.quiet {
  background: repeating-linear-gradient(135deg, transparent 0 5px, var(--border-strong) 5px 6px);
}
.block {
  position: absolute;
  top: 6px;
  bottom: 6px;
  border-radius: 4px;
  border: 1px solid var(--accent-line);
  background: var(--surface);
  color: var(--text);
  font-size: 11px;
  padding: 0 6px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-align: start;
  cursor: default;
  box-shadow: 0 0 0 2px var(--surface-2);
}
.block.running {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--on-accent);
}
.block:focus-visible {
  outline: 2px solid var(--accent-ring);
  outline-offset: 1px;
}
.nothing {
  position: absolute;
  inset-inline-start: 10px;
  top: 11px;
}
.tip {
  position: absolute;
  top: 42px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 220px;
  max-width: 340px;
  padding: 8px 10px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  font-size: 12px;
}
.spool ul {
  list-style: none;
  margin: 0 0 6px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.spool li {
  display: flex;
  gap: 6px;
  align-items: center;
}
.queue .actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.jid {
  display: block;
}
.pin {
  min-height: var(--control-h-sm);
  height: var(--control-h-sm);
  font-size: 12px;
}
.tag {
  margin-inline-start: 6px;
  font-size: 11px;
  padding: 0 6px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--muted);
}
.tag.accent {
  border-color: var(--accent-line);
  color: var(--text);
}
tr.held td {
  color: var(--muted);
}
.err {
  color: var(--danger-text);
}
@media (max-width: 640px) {
  .axis,
  .lane {
    grid-template-columns: 1fr;
    gap: 4px;
  }
  .axis .lane-head {
    display: none;
  }
}
</style>
