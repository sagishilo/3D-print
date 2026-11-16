<script setup lang="ts">
// Order Pipeline (design screen 2): KPIs, the board (drag = dispatch) or a table, filters, nameplate generation and
// the build plates the Fleet Manager assigned. Orders open in a panel with every action their stage allows.
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import NewOrderDialog from '@/components/orders/NewOrderDialog.vue'
import OrderBoard from '@/components/orders/OrderBoard.vue'
import OrderDrawer from '@/components/orders/OrderDrawer.vue'
import Icon from '@/components/ui/Icon.vue'
import KpiCard from '@/components/ui/KpiCard.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import TabBar from '@/components/ui/TabBar.vue'
import { errorText } from '@/lib/api'
import { stageLabel } from '@/lib/events'
import { ago, dateTime, duration, shekels } from '@/lib/format'
import type { OrderCard } from '@/lib/order-types'
import { CHANNELS, type DropAction, type DueFilter, dueMatches, itemsSummary } from '@/lib/orders'
import { useLive } from '@/stores/live'
import { useOrders } from '@/stores/orders'
import { usePrinters } from '@/stores/printers'
import { useSystem } from '@/stores/system'
import { useToasts } from '@/stores/toasts'

const route = useRoute()
const router = useRouter()
const orders = useOrders()
const printers = usePrinters()
const system = useSystem()
const live = useLive()
const toasts = useToasts()

onMounted(() => {
  void orders.load()
  if (!system.settings.length) void system.loadSettings()
})

// ---------------------------------------------------------------- filters
const view = ref('board')
const channel = ref('')
const due = ref<DueFilter>('all')
const stuckOnly = ref(false)
const shown = computed(() =>
  orders.cards.filter((c) => (!channel.value || c.channel === channel.value) && dueMatches(c.due_by, due.value) && (!stuckOnly.value || c.stuck)),
)
const open = computed(() => orders.cards.filter((c) => !['SHIPPED', 'CANCELLED'].includes(c.state)))

// ---------------------------------------------------------------- KPIs (design: open, in print, QC fails, lead -> ship)
const kpis = computed(() => {
  const s = orders.stats
  const dueToday = open.value.filter((c) => dueMatches(c.due_by, 'today')).length
  const printing = orders.plates.filter((p) => p.status === 'printing').length
  const qcPct = s && s.qc_checked_units_7d ? ((s.qc_failed_units_7d / s.qc_checked_units_7d) * 100).toFixed(1) : null
  const delta = s?.lead_to_ship_days != null && s.lead_to_ship_days_prev != null ? s.lead_to_ship_days - s.lead_to_ship_days_prev : null
  return [
    { label: 'Open orders', value: String(open.value.length), sub: `${dueToday} due today`, tone: dueToday ? ('warn' as const) : ('idle' as const) },
    { label: 'In print', value: String(open.value.filter((c) => c.state === 'PRINTING').length), sub: `${printing} plate${printing === 1 ? '' : 's'} active` },
    { label: 'QC fails · 7 d', value: String(s?.qc_failed_units_7d ?? '—'), sub: qcPct ? `${qcPct}% of units` : 'no checks yet' },
    {
      label: 'Lead → ship',
      value: s?.lead_to_ship_days != null ? `${s.lead_to_ship_days} d` : '—',
      sub: delta != null ? `${delta <= 0 ? '−' : '+'}${Math.abs(delta).toFixed(1)} d vs last month` : 'last 30 days',
      tone: delta == null ? ('idle' as const) : delta <= 0 ? ('ok' as const) : ('warn' as const),
    },
  ]
})

const autoSwitches = computed(() => system.settings.filter((s) => s.key.startsWith('orders.auto_')))

// ---------------------------------------------------------------- drawer, new order, drops
const openId = computed(() => (typeof route.query.order === 'string' ? route.query.order : null))
const openOrder = (id: string) => void router.replace({ query: { ...route.query, order: id } })
function closeOrder() {
  const { order: _drop, ...rest } = route.query
  void router.replace({ query: rest })
}
const creating = ref(false)

async function onDrop(card: OrderCard, action: DropAction) {
  if (!action.ok) return toasts.error(`Order ${card.order_id} not moved`, action.reason)
  if (action.kind === 'ship' || action.kind === 'retry_texts') return openOrder(card.order_id)
  try {
    await orders.advance(card.order_id, action.to)
    toasts.info(`Order ${card.order_id} → ${stageLabel(action.to)}`, 'Dispatched; the board updates when the coordinator confirms.')
  } catch (err) {
    toasts.error(`Order ${card.order_id} not moved`, errorText(err))
  }
}

// ---------------------------------------------------------------- nameplates and plates
const nameplates = computed(() =>
  orders.cards
    .filter((c) => ['RECEIVED', 'PERSONALIZING'].includes(c.state) || (c.state === 'NEEDS_ATTENTION' && c.stage === 'PERSONALIZING'))
    .flatMap((c) => c.items.map((i) => ({ card: c, item: i }))),
)
const MODEL_TONE = { ready: 'ok', failed: 'danger', generating: 'accent', pending: 'idle' } as const
const plates = computed(() =>
  orders.plates.map((p) => {
    const byOrder = new Map<string, number>()
    for (const j of p.jobs) byOrder.set(j.order_id, (byOrder.get(j.order_id) ?? 0) + 1)
    const state = printers.get(p.printer_id)?.state
    const progress = p.status === 'printing' && state?.plate_id === p.plate_id ? Math.round(state.progress * 100) : null
    return { ...p, contents: [...byOrder].map(([o, n]) => `#${o.replace(/^ord-/, '')} ×${n}`).join(', '), progress }
  }),
)
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Order Pipeline</h1>
      <p>Drag to move · dropping into Personalization or Slicing dispatches a NATS job · Printing is driven by printers</p>
    </div>
    <div class="head-actions">
      <button type="button" class="btn btn-primary" data-testid="new-order-button" @click="creating = true"><Icon name="plus" :size="14" />New manual order</button>
    </div>
  </div>

  <div class="kpis">
    <KpiCard v-for="k in kpis" :key="k.label" :label="k.label" :value="k.value" :sub="k.sub" :sub-tone="k.tone" />
  </div>

  <div class="toolbar">
    <TabBar v-model="view" :tabs="[{ id: 'board', label: 'Board' }, { id: 'table', label: 'Table' }]" label="Order view" />
    <div class="filters">
      <label class="sr-only" for="f-channel">Channel</label>
      <select id="f-channel" v-model="channel" class="select">
        <option value="">Channel: all</option>
        <option v-for="c in CHANNELS" :key="c" :value="c">{{ c }}</option>
      </select>
      <label class="sr-only" for="f-due">Due</label>
      <select id="f-due" v-model="due" class="select">
        <option value="all">Due: any time</option>
        <option value="today">Due: today</option>
        <option value="week">Due: this week</option>
        <option value="overdue">Overdue</option>
      </select>
      <button type="button" class="btn btn-sm" data-tour="stuck-filter" :class="{ on: stuckOnly }" :aria-pressed="stuckOnly" @click="stuckOnly = !stuckOnly">Stuck &gt; 2 h</button>
    </div>
  </div>

  <p class="auto" data-tour="auto-advance">
    Auto-advance:
    <RouterLink v-for="s in autoSwitches" :key="s.key" :to="{ path: '/system', query: { tab: 'config' } }" class="chip" :class="{ accent: s.value }">
      {{ s.label }} · {{ s.value ? 'on' : 'off' }}
    </RouterLink>
  </p>

  <p v-if="orders.error" class="err" role="alert">{{ orders.error }}</p>

  <OrderBoard v-if="view === 'board'" :cards="shown" @open="openOrder" @drop="onDrop" />

  <section v-else class="panel">
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th>Order</th><th>Customer</th><th>Channel</th><th>Items</th><th>Stage</th><th>Status</th><th>Due</th><th class="num">Total</th><th class="num">Paid</th><th>In stage</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in shown" :key="c.order_id" class="selectable" tabindex="0" @click="openOrder(c.order_id)" @keydown.enter="openOrder(c.order_id)">
            <td class="mono">#{{ c.order_id.replace(/^ord-/, '') }}</td>
            <td>{{ c.customer_name }}</td>
            <td>{{ c.channel }}</td>
            <td>{{ itemsSummary(c) }}</td>
            <td><StatusPill :tone="c.state === 'NEEDS_ATTENTION' ? 'danger' : c.state === 'SHIPPED' ? 'ok' : 'accent'" :label="stageLabel(c.state)" /></td>
            <td class="wrap">{{ c.status }}</td>
            <td class="num">{{ c.due_by ? dateTime(c.due_by) : '—' }}</td>
            <td class="num">{{ shekels(c.total_agorot) }}</td>
            <td class="num">{{ shekels(c.paid_agorot) }}</td>
            <td class="num" :class="{ 'text-warn': c.stuck }">{{ ago(new Date(c.stage_since).getTime(), live.now) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!shown.length" class="empty">No orders match these filters.</p>
    </div>
  </section>

  <div class="grid-2">
    <section class="panel" data-testid="nameplates">
      <div class="panel-head"><h2>Nameplate generation</h2><span class="panel-note">Personalization agent · OpenSCAD</span></div>
      <div class="table-wrap">
        <table class="data compact">
          <thead><tr><th>Order</th><th>Customer</th><th>Text</th><th>Status</th><th /></tr></thead>
          <tbody>
            <tr v-for="{ card, item } in nameplates" :key="`${card.order_id}-${item.item_id}`">
              <td class="mono">#{{ card.order_id.replace(/^ord-/, '') }}·{{ item.item_id }}</td>
              <td>{{ card.customer_name }}</td>
              <td><bdi>“{{ item.text }}”</bdi> <span class="muted">· {{ item.color }}</span></td>
              <td>
                <StatusPill :tone="MODEL_TONE[item.model.status ?? 'pending']" :label="item.model.status === 'failed' ? `failed · ${item.model.error}` : (item.model.status ?? 'waiting')" />
              </td>
              <td><button v-if="item.model.status === 'failed'" type="button" class="btn btn-sm" @click="openOrder(card.order_id)">Retry</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="!nameplates.length" class="empty">No nameplate waiting.</p>
      </div>
    </section>

    <section class="panel" data-testid="plates">
      <div class="panel-head"><h2>Auto-assigned build plates</h2><span class="panel-note">Fleet Manager packs units by material and color</span></div>
      <div class="table-wrap">
        <table class="data compact">
          <thead><tr><th>Plate</th><th>Printer</th><th>Contents</th><th>Filament</th><th>Progress</th></tr></thead>
          <tbody>
            <tr v-for="p in plates" :key="p.plate_id">
              <td class="mono">{{ p.plate_id.replace(/^plate-/, 'P-').slice(0, 10) }}</td>
              <td>{{ p.printer_id }}</td>
              <td>{{ p.contents }}</td>
              <td>{{ p.material }} {{ p.color }}</td>
              <td class="num">{{ p.progress != null ? `${p.progress}%` : `${p.status} · ~${duration(p.est_minutes * 60)}` }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!plates.length" class="empty">No plate scheduled or printing.</p>
      </div>
    </section>
  </div>

  <NewOrderDialog v-if="creating" @close="creating = false" />
  <OrderDrawer v-if="openId" :order-id="openId" @close="closeOrder" />
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.filters .select {
  min-height: 32px;
  font-size: 13px;
}
.btn.on {
  border-color: var(--warn);
  color: var(--warn);
}
.auto {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--muted);
}
.auto .chip {
  text-decoration: none;
}
.err {
  color: var(--danger);
}
.compact {
  min-width: 0;
}
.compact td,
.data td {
  white-space: nowrap;
}
td.wrap {
  white-space: normal;
}
</style>
