<script setup lang="ts">
// Filament (design screen 5): KPIs, alerts, spools (live grams while printing), the reorder list with editable rules
// and "Order now", purchase orders (approve, change quantity, confirm ordered, receive) and daily use.
import { computed, onMounted, reactive, ref } from 'vue'

import SpoolCard from '@/components/filament/SpoolCard.vue'
import SpoolDialog from '@/components/filament/SpoolDialog.vue'
import Icon from '@/components/ui/Icon.vue'
import KpiCard from '@/components/ui/KpiCard.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { errorText } from '@/lib/api'
import { ALERT_LABEL, dailyTotals, inPrintGrams, type MaterialRow, type PurchaseOrder } from '@/lib/filament'
import { dateTime, shekels } from '@/lib/format'
import { toAgorot } from '@/lib/orders'
import type { Tone } from '@/lib/types'
import { useApprovals } from '@/stores/approvals'
import { useConfirm } from '@/stores/confirm'
import { useInventory } from '@/stores/inventory'
import { usePrinters } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'

const inventory = useInventory()
const printers = usePrinters()
const approvals = useApprovals()
const confirm = useConfirm()
const toasts = useToasts()

onMounted(() => void inventory.load())

// ---------------------------------------------------------------- spools
const showEmpty = ref(false)
const shownSpools = computed(() => inventory.spools.filter((s) => showEmpty.value || s.status !== 'empty' || s.printer_id))
const inPrint = computed(() => inPrintGrams(inventory.spools, inventory.reservations, printers.list))
const daily = (m: string, c: string) => inventory.materials.find((x) => x.material === m && x.color === c)?.daily_grams ?? null
const dialog = ref<{ spoolId: string | null; tag?: string } | null>(null)
const dialogSpool = computed(() => (dialog.value?.spoolId ? (inventory.spools.find((s) => s.spool_id === dialog.value!.spoolId) ?? null) : null))
const scan = ref('')
function onScan() {
  const code = scan.value.trim()
  if (!code) return
  const found = inventory.spools.find((s) => s.tag === code || s.spool_id === code)
  dialog.value = found ? { spoolId: found.spool_id } : { spoolId: null, tag: code }
  scan.value = ''
}

// ---------------------------------------------------------------- KPIs
const kpis = computed(() => {
  const active = inventory.spools.filter((s) => s.status === 'loaded')
  const below = inventory.materials.filter((m) => m.below_min)
  return [
    { label: 'Active spools', value: String(active.length), sub: `across ${new Set(active.map((s) => s.printer_id)).size} printers` },
    { label: 'Backup spools', value: String(inventory.spools.filter((s) => s.status === 'stock').length), sub: 'on shelf' },
    {
      label: 'Below threshold',
      value: String(below.length),
      sub: below.length ? below.map((m) => `${m.material} ${m.color}`).join(', ') : 'all above minimum',
      tone: below.length ? ('warn' as const) : ('ok' as const),
    },
    { label: 'Spend · 30 d', value: shekels(inventory.spend30d), sub: `${inventory.ordered30d} PO${inventory.ordered30d === 1 ? '' : 's'}` },
  ]
})

async function run(label: string, fn: () => Promise<unknown>, note = 'The inventory agent confirms on the live feed.') {
  try {
    await fn()
    toasts.info(label, note)
    inventory.reloadSoon()
  } catch (err) {
    toasts.error(`${label} failed`, errorText(err))
  }
}

// ---------------------------------------------------------------- reorder rules
const STATE: Record<MaterialRow['state'], { tone: Tone; label: (m: MaterialRow) => string }> = {
  triggered: { tone: 'danger', label: () => 'Triggered' },
  soon: { tone: 'warn', label: (m) => `In ${m.days_to_trigger} d` },
  ok: { tone: 'ok', label: () => 'OK' },
}
const editing = ref<string | null>(null)
const rule = reactive({ min: '', mode: 'auto', supplier: '', lead: '' })
const key = (m: MaterialRow) => `${m.material}/${m.color}`
function edit(m: MaterialRow) {
  editing.value = key(m)
  Object.assign(rule, { min: String(m.min_stock_grams), mode: m.mode, supplier: m.supplier, lead: String(m.lead_time_days) })
}
function saveRule(m: MaterialRow) {
  editing.value = null
  return run(`Rule ${m.material} ${m.color} saved`, () =>
    inventory.setRule({ material: m.material, color: m.color, min_stock_grams: Number(rule.min), mode: rule.mode, supplier: rule.supplier, lead_time_days: Number(rule.lead) }),
  )
}
async function orderNow(m: MaterialRow) {
  const n = m.suggested_spools || 1
  const ok = await confirm.ask({
    title: `Order ${m.material} ${m.color}?`,
    text: `A purchase order for ${n} spool${n === 1 ? '' : 's'} (~${shekels(n * m.cost_per_spool_agorot)}) is placed now${m.supplier ? ` for ${m.supplier}` : ''}. You then order it from the supplier and confirm the real total below.`,
    confirmLabel: 'Place order',
  })
  if (ok) await run(`Order for ${m.material} ${m.color} placed`, () => inventory.reorder(m.material, m.color, n))
}

// ---------------------------------------------------------------- purchase orders
const PO_LABEL: Record<PurchaseOrder['status'], { tone: Tone; label: string }> = {
  pending_approval: { tone: 'warn', label: 'needs approval' },
  placed: { tone: 'accent', label: 'to order' },
  ordered: { tone: 'accent', label: 'ordered' },
  received: { tone: 'ok', label: 'received' },
  rejected: { tone: 'idle', label: 'dismissed' },
}
const poForm = reactive<Record<string, { qty: string; supplier: string; total: string; shelf: string }>>({})
const form = (po: PurchaseOrder) =>
  (poForm[po.po_id] ??= { qty: String(po.spools), supplier: inventory.materials.find((m) => m.material === po.material && m.color === po.color)?.supplier ?? '', total: (po.est_cost_agorot / 100).toFixed(2), shelf: '' })
const awaiting = computed(() => inventory.pos.filter((p) => p.status === 'pending_approval').length)
const decide = (po: PurchaseOrder, approved: boolean) =>
  run(approved ? 'PO approved' : 'PO dismissed', () => approvals.decide(po.approval_id as string, approved))

// ---------------------------------------------------------------- usage chart (14 days)
const totals = computed(() => dailyTotals(inventory.usage, 14))
const maxDay = computed(() => Math.max(100, ...totals.value.map((d) => d.grams)))
const avg = computed(() => Math.round(totals.value.reduce((a, d) => a + d.grams, 0) / 14))
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Filament Inventory &amp; Auto-Reorder</h1>
      <p>Grams booked per finished plate · active spools count down live while they print · reorder when grams − rate × (lead + buffer) ≤ min</p>
    </div>
    <form class="scan" data-tour="spool-scan" @submit.prevent="onScan">
      <label class="sr-only" for="scan">Scan a spool label</label>
      <input id="scan" v-model="scan" class="input" placeholder="Scan or type a spool label" autocomplete="off" data-testid="scan" />
      <button type="submit" class="btn"><Icon name="search" :size="14" />Find</button>
      <button type="button" class="btn btn-primary" data-testid="register-spool" @click="dialog = { spoolId: null }"><Icon name="plus" :size="14" />Register spool</button>
    </form>
  </div>

  <div class="kpis" data-tour="filament-kpis">
    <KpiCard v-for="k in kpis" :key="k.label" :label="k.label" :value="k.value" :sub="k.sub" :sub-tone="k.tone" />
  </div>

  <p v-if="inventory.error" class="err" role="alert">{{ inventory.error }}</p>

  <section v-if="inventory.openAlerts.length" class="alerts" data-testid="inventory-alerts">
    <div v-for="a in inventory.openAlerts" :key="a.alert_id" class="alert" :class="a.severity">
      <Icon name="alert" :size="16" />
      <div class="text">
        <b>{{ ALERT_LABEL[a.kind] }}</b>
        <span>{{ a.message }}</span>
        <span class="muted small">since {{ dateTime(a.opened_at) }}</span>
      </div>
      <button type="button" class="btn btn-sm" @click="run('Alert acknowledged', () => inventory.ackAlert(a.alert_id))">Acknowledge</button>
    </div>
  </section>

  <section class="panel" data-tour="spools">
    <div class="panel-head">
      <h2>Spools</h2>
      <label class="small muted"><input v-model="showEmpty" type="checkbox" /> show empty spools</label>
    </div>
    <div class="spools">
      <SpoolCard
        v-for="s in shownSpools"
        :key="s.spool_id"
        :spool="s"
        :in-print="inPrint.get(s.spool_id)"
        :daily-grams="daily(s.material, s.color)"
        @open="dialog = { spoolId: $event }"
      />
    </div>
    <p v-if="!shownSpools.length" class="empty">No spools yet: register one.</p>
  </section>

  <section class="panel" data-testid="reorder-rules">
    <div class="panel-head">
      <h2>Reorder rules</h2>
      <span class="panel-note">Auto: orders under the approval limit go out on their own · Manual: every order waits for you</span>
    </div>
    <div class="table-wrap">
      <table class="data rules">
        <thead>
          <tr>
            <th>Material</th><th class="num">Consumption</th><th class="num">On hand</th><th class="num">Min stock</th><th>Supplier · lead time</th><th>Mode</th><th>State</th><th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in inventory.materials" :key="key(m)" :data-testid="`rule-${m.material}-${m.color}`">
            <td><b>{{ m.material }} {{ m.color }}</b><span class="muted small"> · {{ m.spools_loaded }} active, {{ m.spools_stock }} spare{{ m.on_order_spools ? `, ${m.on_order_spools} on order` : '' }}</span></td>
            <td class="num">{{ m.daily_grams }} g/d</td>
            <td class="num" :class="{ 'text-warn': m.below_min }">{{ Math.round(m.on_hand_grams) }} g<span class="muted small"> · {{ m.days_left ?? '—' }} d</span></td>
            <template v-if="editing === key(m)">
              <td><input v-model="rule.min" class="input small-input" inputmode="numeric" aria-label="Minimum stock in grams" data-testid="rule-min" /></td>
              <td>
                <input v-model="rule.supplier" class="input" aria-label="Supplier" />
                <input v-model="rule.lead" class="input small-input" inputmode="decimal" aria-label="Lead time in days" /> d
              </td>
              <td>
                <select v-model="rule.mode" class="select" aria-label="Mode"><option value="auto">Auto</option><option value="manual">Manual</option></select>
              </td>
              <td />
              <td class="actions"><button type="button" class="btn btn-sm btn-primary" @click="saveRule(m)">Save</button><button type="button" class="btn btn-sm" @click="editing = null">Cancel</button></td>
            </template>
            <template v-else>
              <td class="num">{{ Math.round(m.min_stock_grams) }} g</td>
              <td>{{ m.supplier || '—' }} · {{ m.lead_time_days }} d</td>
              <td>{{ m.mode === 'auto' ? 'Auto' : 'Manual' }}</td>
              <td><StatusPill :tone="STATE[m.state].tone" :label="STATE[m.state].label(m)" /></td>
              <td class="actions">
                <button type="button" class="btn btn-sm" @click="edit(m)">Edit</button>
                <button type="button" class="btn btn-sm" :data-testid="`order-${m.material}-${m.color}`" @click="orderNow(m)">Order now</button>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <div class="grid-2">
    <section class="panel" data-testid="purchase-orders">
      <div class="panel-head">
        <h2>Purchase orders</h2>
        <span class="panel-note">{{ awaiting }} awaiting approval</span>
      </div>
      <article v-for="po in inventory.pos" :key="po.po_id" class="po">
        <div class="row">
          <b>{{ po.po_id }}{{ po.supplier ? ` · ${po.supplier}` : '' }}</b>
          <StatusPill :tone="PO_LABEL[po.status].tone" :label="PO_LABEL[po.status].label" />
        </div>
        <span>{{ po.material }} {{ po.color }} × {{ po.spools }} · {{ shekels(po.total_agorot ?? po.est_cost_agorot) }}{{ po.total_agorot ? '' : ' (estimate)' }}</span>
        <span class="muted small">created {{ dateTime(po.created_at) }}{{ po.ordered_at ? ` · ordered ${dateTime(po.ordered_at)}` : '' }}{{ po.received_at ? ` · received ${dateTime(po.received_at)}` : '' }}</span>
        <div v-if="po.status === 'pending_approval'" class="row actions">
          <label class="inline">Spools <input v-model="form(po).qty" class="input small-input" inputmode="numeric" /></label>
          <button type="button" class="btn btn-sm" @click="run('Quantity changed', () => inventory.updatePo(po.po_id, Number(form(po).qty)))">Edit qty</button>
          <button type="button" class="btn btn-sm btn-primary" @click="decide(po, true)"><Icon name="check" :size="14" />Approve</button>
          <button type="button" class="btn btn-sm" @click="decide(po, false)">Dismiss</button>
        </div>
        <div v-else-if="po.status === 'placed'" class="row actions">
          <input v-model="form(po).supplier" class="input" placeholder="Supplier" aria-label="Supplier" />
          <input v-model="form(po).total" class="input small-input" inputmode="decimal" aria-label="Invoice total in shekels" />
          <button type="button" class="btn btn-sm btn-primary" @click="run('PO confirmed as ordered', () => inventory.confirmOrdered(po.po_id, form(po).supplier, toAgorot(form(po).total)), 'Finance books it as an expense.')">Confirm ordered</button>
        </div>
        <div v-else-if="po.status === 'ordered'" class="row actions">
          <input v-model="form(po).shelf" class="input small-input" placeholder="Shelf" aria-label="Shelf" />
          <button type="button" class="btn btn-sm btn-primary" @click="run('PO received', () => inventory.receive(po.po_id, form(po).shelf))">Receive</button>
        </div>
      </article>
      <p v-if="!inventory.pos.length" class="empty">No purchase orders in the last 30 days.</p>
    </section>

    <section class="panel" data-testid="usage">
      <div class="panel-head">
        <h2>Filament used per day · all materials</h2>
        <span class="panel-note">today {{ totals[totals.length - 1]?.grams ?? 0 }} g · 14-day avg {{ avg }} g</span>
      </div>
      <svg class="chart" viewBox="0 0 280 120" role="img" :aria-label="`Filament used per day over 14 days, average ${avg} grams`">
        <rect v-for="(d, i) in totals" :key="d.day" :x="i * 20 + 3" :y="110 - (d.grams / maxDay) * 100" width="14" :height="(d.grams / maxDay) * 100" rx="2">
          <title>{{ d.day }}: {{ d.grams }} g</title>
        </rect>
        <line x1="0" x2="280" y1="110" y2="110" class="axis" />
      </svg>
      <div class="row muted small"><span>{{ totals[0]?.day }}</span><span>{{ totals[totals.length - 1]?.day }}</span></div>
    </section>
  </div>

  <SpoolDialog v-if="dialog" :spool="dialogSpool" :tag="dialog.tag" @close="dialog = null" />
</template>

<style scoped>
.scan {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.scan .input {
  min-width: 220px;
}
.err {
  color: var(--danger);
}
.alerts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.alert {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--warn);
  background: var(--warn-bg);
  color: var(--warn);
}
.alert.info {
  border-color: var(--border);
  background: var(--surface);
  color: var(--muted);
}
.alert .text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--text);
  font-size: 13px;
}
.spools {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(210px, 100%), 1fr));
  gap: 12px;
}
.small {
  font-size: 12px;
}
.rules {
  min-width: 760px;
}
.rules td {
  white-space: nowrap;
}
.small-input {
  width: 80px;
}
.actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.po {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.row.actions {
  justify-content: flex-start;
}
.inline {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.chart {
  width: 100%;
  height: 160px;
}
.chart rect {
  fill: var(--accent);
}
.axis {
  stroke: var(--border);
}
</style>
