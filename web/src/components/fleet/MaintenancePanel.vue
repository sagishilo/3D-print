<script setup lang="ts">
// A printer's maintenance (Wave 2 / W2-C): its counters, the auto plate-swap policy, interval tasks (due / overdue,
// Done) and what was done before. Changes are commands to the fleet agent; owners add and remove tasks.
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { vCan } from '@/directives/can'
import { errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { type MaintenanceTask, taskId, useSchedule } from '@/stores/schedule'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ printerId: string }>()
const { t } = useI18n()
const schedule = useSchedule()
const confirm = useConfirm()
const auth = useAuth()
const toasts = useToasts()
const error = ref('')

const m = computed(() => schedule.maintenance[props.printerId] ?? null)
async function load() {
  try {
    await schedule.loadMaintenance(props.printerId)
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  }
}
onMounted(load)
watch(() => props.printerId, load)

async function send(p: Promise<unknown>, what: string) {
  try {
    await p
    toasts.info(t('maintenance.saved'), what)
    schedule.reloadSoon(700)
  } catch (err) {
    toasts.error(what, errorText(err))
  }
}

const every = (x: MaintenanceTask) => [
  x.every_hours ? t('maintenance.hours', { n: x.every_hours }) : '',
  x.every_plates ? t('maintenance.platesN', { n: x.every_plates }) : '',
  x.every_days ? t('maintenance.days', { n: x.every_days }) : '',
].filter(Boolean).join(' · ')

async function done(x: MaintenanceTask) {
  const ok = await confirm.ask({ title: x.label, text: t('maintenance.doneConfirm', { task: x.label, p: props.printerId }),
    confirmLabel: t('maintenance.done') })
  if (ok) await send(schedule.done(props.printerId, x.task_id), x.label)
}

async function refill() {
  const n = m.value?.policy.plate_stack ?? 0
  const ok = await confirm.ask({ title: t('maintenance.refill'), text: t('maintenance.refillConfirm', { p: props.printerId, n }),
    confirmLabel: t('maintenance.refill') })
  if (ok) await send(schedule.policy(props.printerId, { refill: true }), props.printerId)
}

const stack = ref<number | null>(null)
watch(m, (v) => { if (v && stack.value === null) stack.value = v.policy.plate_stack }, { immediate: true })

// Owners: add a task
const draft = ref({ label: '', hours: null as number | null, plates: null as number | null, days: null as number | null, block: false })
const draftError = ref('')
async function addTask() {
  const d = draft.value
  if (!d.label.trim()) return
  if (!d.hours && !d.plates && !d.days) {
    draftError.value = t('maintenance.needInterval')
    return
  }
  draftError.value = ''
  await send(schedule.saveTask({ printer_id: props.printerId, task_id: taskId(d.label), label: d.label.trim(),
    every_hours: d.hours || null, every_plates: d.plates || null, every_days: d.days || null, block_when_overdue: d.block }), d.label)
  draft.value = { label: '', hours: null, plates: null, days: null, block: false }
}

async function archive(x: MaintenanceTask) {
  await send(schedule.saveTask({ printer_id: x.printer_id, task_id: x.task_id, label: x.label, every_hours: x.every_hours,
    every_plates: x.every_plates, every_days: x.every_days, block_when_overdue: x.block_when_overdue, archived: true }), x.label)
}
</script>

<template>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div v-if="m" class="maint" data-testid="maintenance-panel">
    <section>
      <h3>{{ t('maintenance.counters') }}</h3>
      <p class="counters" data-testid="maintenance-counters">
        <span>{{ t('maintenance.printHours', { n: m.counters.print_hours }) }}</span>
        <span>{{ t('maintenance.plates', { n: m.counters.plates }) }}</span>
        <span>{{ t('maintenance.kg', { n: m.counters.kg }) }}</span>
      </p>
    </section>

    <section data-testid="plate-swap">
      <h3>{{ t('maintenance.plateSwap') }}</h3>
      <p class="muted small">{{ t('maintenance.plateSwapHelp') }}</p>
      <div class="row">
        <label class="switch">
          <input v-can="'operator'" type="checkbox" :checked="m.policy.auto_eject" data-testid="auto-eject"
            @change="send(schedule.policy(printerId, { auto_eject: ($event.target as HTMLInputElement).checked }), printerId)" />
          {{ t('maintenance.autoEject') }}
        </label>
        <label>{{ t('maintenance.stack') }}
          <input v-model.number="stack" v-can="'operator'" class="input num" type="number" min="0" max="100" data-testid="plate-stack"
            @change="stack !== null && send(schedule.policy(printerId, { plate_stack: stack }), printerId)" />
        </label>
        <span v-if="m.policy.auto_eject" class="small" data-testid="plates-left">{{ t('maintenance.left', { n: m.policy.plates_left, total: m.policy.plate_stack }) }}</span>
        <button v-if="m.policy.auto_eject" v-can="'operator'" type="button" class="btn btn-sm" data-testid="refill" @click="refill">{{ t('maintenance.refill') }}</button>
      </div>
    </section>

    <section>
      <h3>{{ t('maintenance.tasks') }}</h3>
      <ul class="tasks">
        <li v-for="x in m.tasks" :key="x.task_id" :class="x.state" :data-testid="`task-${x.task_id}`">
          <div class="what">
            <b>{{ x.label }}</b>
            <span class="muted small">{{ t('maintenance.every', { what: every(x) }) }}<template v-if="x.block_when_overdue"> · {{ t('maintenance.blocks') }}</template></span>
            <span class="muted small">{{ x.last_done_at ? t('maintenance.lastDone', { t: dateTime(x.last_done_at) }) : t('maintenance.neverDone') }}</span>
          </div>
          <div class="meter" role="meter" :aria-valuenow="Math.round(Math.min(x.used, 1) * 100)" aria-valuemin="0" aria-valuemax="100" :aria-label="x.label">
            <i :style="{ width: `${Math.min(x.used, 1) * 100}%` }" />
          </div>
          <span class="state small" :data-testid="`task-state-${x.task_id}`">{{ t(`maintenance.state.${x.state}`) }} · {{ x.state === 'overdue' ? t('maintenance.overText', { left: x.left }) : t('maintenance.leftText', { left: x.left }) }}</span>
          <button v-can="'operator'" type="button" class="btn btn-sm" :class="{ 'btn-primary': x.state !== 'ok' }" :data-testid="`task-done-${x.task_id}`" @click="done(x)">{{ t('maintenance.done') }}</button>
          <button v-can="'owner'" type="button" class="link small" :data-testid="`task-archive-${x.task_id}`" @click="archive(x)">{{ t('maintenance.archive') }}</button>
        </li>
      </ul>
      <form v-if="auth.can('owner')" class="add" data-testid="task-add" @submit.prevent="addTask">
        <b class="small">{{ t('maintenance.add') }}</b>
        <input v-model="draft.label" class="input" :placeholder="t('maintenance.label')" :aria-label="t('maintenance.label')" maxlength="80" data-testid="task-label" />
        <input v-model.number="draft.hours" class="input num" type="number" min="1" :placeholder="t('maintenance.everyHours')" :aria-label="t('maintenance.everyHours')" data-testid="task-hours" />
        <input v-model.number="draft.plates" class="input num" type="number" min="1" :placeholder="t('maintenance.everyPlates')" :aria-label="t('maintenance.everyPlates')" data-testid="task-plates" />
        <input v-model.number="draft.days" class="input num" type="number" min="1" :placeholder="t('maintenance.everyDays')" :aria-label="t('maintenance.everyDays')" data-testid="task-days" />
        <label class="switch small"><input v-model="draft.block" type="checkbox" />{{ t('maintenance.block') }}</label>
        <button type="submit" class="btn btn-sm" data-testid="task-save">{{ t('maintenance.saveTask') }}</button>
        <span v-if="draftError" class="error small" role="alert">{{ draftError }}</span>
      </form>
    </section>

    <section>
      <h3>{{ t('maintenance.log') }}</h3>
      <ul v-if="m.log.length" class="log small" data-testid="maintenance-log">
        <li v-for="(l, i) in m.log" :key="i">{{ dateTime(l.at) }} · <b>{{ l.label }}</b> · {{ l.done_by }}<template v-if="l.note"> · {{ l.note }}</template></li>
      </ul>
      <p v-else class="muted small">{{ t('maintenance.noLog') }}</p>
    </section>
  </div>
</template>

<style scoped>
.maint {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
h3 {
  margin: 0 0 6px;
  font-size: 13px;
}
.counters {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin: 0;
}
.row,
.add {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.row label {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
}
.switch {
  display: flex;
  gap: 6px;
  align-items: center;
}
.num {
  width: 110px;
}
.tasks,
.log {
  list-style: none;
  margin: 0 0 10px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tasks li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 90px auto auto auto;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.tasks li.overdue {
  box-shadow: inset calc(3px * var(--dir-x, 1)) 0 0 var(--danger-text);
}
.tasks li.due {
  box-shadow: inset calc(3px * var(--dir-x, 1)) 0 0 var(--warn);
}
.what {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.meter {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}
.meter i {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
}
.overdue .state {
  color: var(--danger-text);
}
.error {
  color: var(--danger-text);
}
@media (max-width: 640px) {
  .tasks li {
    grid-template-columns: 1fr auto;
  }
  .meter {
    grid-column: 1 / -1;
  }
}
</style>
