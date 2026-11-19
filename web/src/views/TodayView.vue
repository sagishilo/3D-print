<script setup lang="ts">
// Today (Wave 1 / M7, redesigned): the home screen. At a glance: printers at work, what is urgent, today's orders
// and revenue, stock alerts; 7 days of momentum and where open orders pile up; then every task across the farm in
// one table (priority, category, what and why, one action), filtered in one click.
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import DayBars from '@/components/today/DayBars.vue'
import RankBars from '@/components/today/RankBars.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import NicheFilterBar from '@/components/ui/NicheFilterBar.vue'
import { vCan } from '@/directives/can'
import { errorText } from '@/lib/api'
import { shekels } from '@/lib/format'
import { nicheColor } from '@/lib/niches'
import { CATEGORY, FILTERS, KIND_ICON, matches, STAGE_SHORT, type TaskFilter, type TodayItem, waited } from '@/lib/today'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { useNiche } from '@/stores/niche'
import { useToday } from '@/stores/today'
import { useToasts } from '@/stores/toasts'

const today = useToday()
const auth = useAuth()
const niche = useNiche()
const confirm = useConfirm()
const toasts = useToasts()
onMounted(() => void today.load())

const s = computed(() => today.summary)
const hello = computed(() => {
  const h = new Date().getHours()
  return `${h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'}${auth.displayName ? `, ${auth.displayName}` : ''}`
})

// ---------------------------------------------------------------- tasks
const tasks = computed<TodayItem[]>(() => [...today.groups.now, ...today.groups.today, ...today.groups.week])
const filter = ref<TaskFilter>('all')
const shown = computed(() => tasks.value.filter((t) => matches(t, filter.value)))
const countOf = (f: TaskFilter) => tasks.value.filter((t) => matches(t, f)).length
const PRIORITY_LABEL = { now: 'Now', today: 'Today', week: 'This week' } as const

// ---------------------------------------------------------------- KPIs and charts
const stockAlerts = computed(() => (s.value ? s.value.alerts.filament + s.value.alerts.parts + s.value.alerts.shelf : 0))
const revenueDays = computed(() => (s.value?.week ?? []).map((d) => ({ day: d.day, value: d.revenue_agorot ?? 0 })))
const orderDays = computed(() => (s.value?.week ?? []).map((d) => ({ day: d.day, value: d.orders })))
const breakdown = ref<'stage' | 'niche'>('stage')
const stageRows = computed(() => (s.value?.stages ?? []).filter((x) => x.count > 0)
  .map((x) => ({ key: x.state, label: STAGE_SHORT[x.state] ?? x.state, value: x.count, to: '/orders' })))
const nicheRows = computed(() => (s.value?.niches ?? []).filter((x) => x.revenue_agorot > 0)
  .map((x) => ({ key: x.niche, label: x.name, value: x.revenue_agorot, color: `var(--niche-${x.color})` })))
const count = (v: number) => String(v)
const money = (v: number) => shekels(v).replace(/\.00$/, '')

async function run(item: TodayItem) {
  if (item.action.type === 'approvals') {
    window.dispatchEvent(new CustomEvent('pf:open-approvals'))
    return
  }
  if (item.action.type !== 'command') return
  const ok = await confirm.ask({ title: item.title, text: item.action.confirm, confirmLabel: item.action.label })
  if (!ok) return
  try {
    await today.act(item)
    toasts.info(`${item.action.label}: sent`, 'Today updates when the agent confirms.')
  } catch (err) {
    toasts.error(`${item.action.label} failed`, errorText(err))
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Today</h1>
      <p>{{ hello }} · {{ today.count ? `${today.count} task${today.count === 1 ? '' : 's'} wait for a person` : 'nothing waits for you' }}</p>
    </div>
    <button type="button" class="btn btn-sm" data-testid="today-refresh" @click="today.load()"><Icon name="refresh" :size="14" />Refresh</button>
  </div>
  <NicheFilterBar what="orders, revenue and stock of this niche · printers and alerts stay farm-wide" />
  <p v-if="today.error" class="err" role="alert">{{ today.error }}</p>

  <!-- ------------------------------------------------ at a glance -->
  <div class="kpis" data-tour="today-kpis">
    <RouterLink class="kpi" to="/live" data-testid="kpi-printers">
      <span class="k-label">Printers at work</span>
      <b class="k-value">{{ s ? `${s.printers.printing}/${s.printers.online}` : '—' }}<small v-if="s"> printing</small></b>
      <span class="meter" role="meter" :aria-valuenow="s?.printers.utilization_pct ?? 0" aria-valuemin="0" aria-valuemax="100" :aria-label="`Utilization ${s?.printers.utilization_pct ?? 0}%`">
        <span :style="{ width: `${s?.printers.utilization_pct ?? 0}%` }" />
      </span>
      <span class="k-sub">{{ s ? `${s.printers.utilization_pct}% busy${s.printers.total > s.printers.online ? ` · ${s.printers.total - s.printers.online} offline` : ''}` : '' }}</span>
    </RouterLink>
    <button type="button" class="kpi" :class="{ urgent: today.urgent > 0 }" :aria-pressed="filter === 'now'" data-testid="kpi-urgent" @click="filter = filter === 'now' ? 'all' : 'now'">
      <span class="k-label">Urgent now</span>
      <b class="k-value">{{ today.urgent }}<small> task{{ today.urgent === 1 ? '' : 's' }}</small></b>
      <span class="k-sub">{{ today.urgent ? 'stops the farm until someone acts' : 'nothing urgent' }} · {{ today.count }} in all</span>
    </button>
    <RouterLink class="kpi" to="/orders" data-testid="kpi-orders">
      <span class="k-label">Today's orders{{ niche.current ? ` · ${niche.name(niche.current)}` : '' }}</span>
      <b class="k-value">{{ s?.orders_today ?? '—' }}<small> order{{ s?.orders_today === 1 ? '' : 's' }}</small></b>
      <span class="k-sub">{{ s?.revenue_today_agorot != null ? `${money(s.revenue_today_agorot)} received today (net)` : 'new orders since midnight' }}</span>
    </RouterLink>
    <RouterLink class="kpi" :class="{ warn: stockAlerts > 0 }" to="/filament" data-testid="kpi-stock">
      <span class="k-label">Stock alerts</span>
      <b class="k-value">{{ s ? stockAlerts : '—' }}<small v-if="s"> open</small></b>
      <span class="k-sub">{{ s ? `${s.alerts.filament} filament · ${s.alerts.parts} parts · ${s.alerts.shelf} shelf` : '' }}</span>
    </RouterLink>
  </div>

  <!-- ------------------------------------------------ momentum and where work piles up -->
  <div v-if="s" class="charts">
    <section class="panel" data-testid="today-trend" data-tour="today-trend">
      <div class="panel-head"><h2>Last 7 days</h2><span class="panel-note">{{ niche.current ? niche.name(niche.current) : 'all niches' }} · today on the right</span></div>
      <div class="multiples">
        <DayBars v-if="s.money" title="Revenue (net)" :points="revenueDays" :format="money" testid="chart-revenue" />
        <DayBars title="New orders" :points="orderDays" :format="count" testid="chart-orders" />
      </div>
    </section>
    <section class="panel" data-testid="today-breakdown" data-tour="today-breakdown">
      <div class="panel-head">
        <h2>{{ breakdown === 'stage' ? 'Open orders by stage' : 'Revenue by niche · 7 days' }}</h2>
        <div v-if="s.money" class="seg" role="group" aria-label="Breakdown">
          <button type="button" :aria-pressed="breakdown === 'stage'" data-testid="breakdown-stage" @click="breakdown = 'stage'">Stage</button>
          <button type="button" :aria-pressed="breakdown === 'niche'" data-testid="breakdown-niche" @click="breakdown = 'niche'">Niche</button>
        </div>
      </div>
      <RankBars v-if="breakdown === 'stage'" :rows="stageRows" :format="count" testid="stages" empty="No open orders." />
      <RankBars v-else :rows="nicheRows" :format="money" testid="niches" empty="No revenue in the last 7 days." />
    </section>
  </div>

  <!-- ------------------------------------------------ tasks -->
  <section class="panel tasks" data-testid="today-tasks" data-tour="today-tasks">
    <div class="panel-head">
      <h2>Tasks</h2>
      <div class="pills" role="tablist" aria-label="Filter tasks" data-tour="today-filters">
        <button v-for="f in FILTERS" :key="f.id" type="button" role="tab" class="pill" :class="{ on: filter === f.id, urgent: f.id === 'now' && countOf('now') > 0 }"
          :aria-selected="filter === f.id" :data-testid="`filter-${f.id}`" @click="filter = f.id">
          {{ f.label }} <span class="n">{{ countOf(f.id) }}</span>
        </button>
      </div>
    </div>
    <div v-if="shown.length" class="table" role="table" aria-label="Tasks">
      <div class="row head" role="row">
        <span role="columnheader">Priority</span><span role="columnheader">Category</span><span role="columnheader">Task</span><span role="columnheader" class="sr-only">Action</span>
      </div>
      <div v-for="i in shown" :key="i.id" class="row" :class="i.priority" role="row" :data-testid="`today-item-${i.id}`">
        <span role="cell"><span class="badge" :class="i.priority">{{ PRIORITY_LABEL[i.priority] }}</span></span>
        <span role="cell" class="cat"><Icon :name="KIND_ICON[i.kind] ?? 'alert'" :size="14" />{{ CATEGORY[i.kind]?.label ?? i.kind }}</span>
        <span role="cell" class="what">
          <b>{{ i.title }}</b>
          <span class="muted small">
            {{ i.why }}
            <template v-if="i.age_s > 600"> · waiting {{ waited(i.age_s) }}</template>
            <span v-if="i.niche" class="niche"><i :style="{ background: nicheColor(niche.bySlug[i.niche]) }" />{{ niche.name(i.niche) }}</span>
            <span v-if="i.expert" class="tech">technical</span>
          </span>
        </span>
        <span role="cell" class="act">
          <RouterLink v-if="i.action.type === 'link'" class="btn btn-sm" :to="i.action.to" :data-testid="`today-go-${i.id}`">{{ i.action.label }}</RouterLink>
          <button v-else v-can="i.min_role" type="button" class="btn btn-sm" :class="{ 'btn-primary': i.priority !== 'week' }" :data-testid="`today-act-${i.id}`" @click="run(i)">{{ i.action.label }}</button>
        </span>
      </div>
    </div>
    <p v-else-if="tasks.length" class="muted empty-filter" data-testid="filter-empty">Nothing in this filter. <button type="button" class="link" @click="filter = 'all'">Show all</button></p>
    <EmptyState v-else-if="today.loaded && !today.error" icon="check" title="All clear"
      text="No bed to clear, no order waiting, nothing low on stock. New work shows here the moment it appears." data-testid="today-clear" />
  </section>
</template>

<style scoped>
/* ---------- KPI strip */
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--gap);
  margin-bottom: var(--gap);
}
.kpi {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  padding: var(--pad-panel);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  text-decoration: none;
  font: inherit;
  cursor: pointer;
}
.kpi:hover {
  border-color: var(--border-strong);
}
.kpi.urgent {
  border-color: var(--danger-text);
  box-shadow: inset 3px 0 0 var(--danger-text);
}
.kpi.warn {
  box-shadow: inset 3px 0 0 var(--warn);
}
.k-label {
  font-size: 12px;
  color: var(--muted);
}
.k-value {
  font: 700 26px var(--font-display);
  line-height: 1.1;
}
.k-value small {
  font: 500 13px var(--font-body, inherit);
  color: var(--muted);
}
.kpi.urgent .k-value {
  color: var(--danger-text);
}
.k-sub {
  font-size: 12px;
  color: var(--muted);
}
.meter {
  display: block;
  height: 6px;
  border-radius: 3px;
  background: var(--surface-2);
  overflow: hidden;
}
.meter span {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
}
/* ---------- charts */
.charts {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--gap);
  margin-bottom: var(--gap);
}
.multiples {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 20px;
}
.seg {
  margin-left: auto;
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 999px;
  overflow: hidden;
}
.seg button {
  background: none;
  border: 0;
  padding: 3px 10px;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
}
.seg button[aria-pressed='true'] {
  background: var(--accent-bg);
  color: var(--text);
}
/* ---------- tasks */
.tasks .panel-head {
  flex-wrap: wrap;
  gap: 10px;
}
.pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-left: auto;
}
.pill {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.pill.on {
  background: var(--accent-bg);
  border-color: var(--accent-line);
  color: var(--text);
}
.pill .n {
  font-variant-numeric: tabular-nums;
  margin-left: 2px;
}
.pill.urgent .n {
  color: var(--danger-text);
  font-weight: 700;
}
.table {
  display: flex;
  flex-direction: column;
}
.row {
  display: grid;
  grid-template-columns: 92px 120px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: var(--cell-y) 4px;
  border-top: 1px solid var(--border);
}
.row.head {
  border-top: 0;
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.row.now {
  background: linear-gradient(90deg, var(--toast-danger, transparent), transparent 40%);
}
.badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--muted);
  background: var(--surface-2);
}
.badge.now {
  background: var(--danger);
  border-color: var(--danger);
  color: var(--on-accent);
}
.badge.today {
  border-color: var(--accent-line);
  color: var(--text);
  background: var(--accent-bg);
}
.cat {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}
.what {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.what b {
  font-size: 14px;
}
.niche {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 6px;
  padding: 0 6px;
  border: 1px solid var(--border);
  border-radius: 999px;
}
.niche i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.tech {
  margin-left: 6px;
  font-size: 11px;
}
.act {
  justify-self: end;
}
.link {
  background: none;
  border: 0;
  padding: 0;
  color: var(--accent);
  cursor: pointer;
  text-decoration: underline;
  font: inherit;
}
.empty-filter {
  padding: 12px 4px;
}
.err {
  color: var(--danger-text);
}
@media (max-width: 1100px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .charts {
    grid-template-columns: minmax(0, 1fr);
  }
}
/* Phones: each task becomes a card (badge and category on top, the task, the action below). */
@media (max-width: 640px) {
  .row {
    grid-template-columns: auto 1fr;
    grid-template-areas: 'pri cat' 'what what' 'act act';
    gap: 6px 10px;
  }
  .row.head {
    display: none;
  }
  .row > :nth-child(1) { grid-area: pri; }
  .row > :nth-child(2) { grid-area: cat; }
  .row > :nth-child(3) { grid-area: what; }
  .row > :nth-child(4) { grid-area: act; justify-self: start; }
  .pills {
    margin-left: 0;
  }
}
@media (max-width: 420px) {
  .kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
