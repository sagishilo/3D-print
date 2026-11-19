<script setup lang="ts">
// Finance & Accounting (design screen 4): the chosen period vs the previous one, the 12-month profit trend,
// expense mix (electricity estimated from print hours until the bill is entered), product margins, receivables,
// the income and expense ledgers, VAT returns and settings. A management view: legal receipts and tax invoices come
// from the invoicing system, whose receipt numbers the income lines keep.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import MoneyDialog from '@/components/finance/MoneyDialog.vue'
import UnitEconomicsPanel from '@/components/finance/UnitEconomicsPanel.vue'
import NicheFilterBar from '@/components/ui/NicheFilterBar.vue'
import Icon from '@/components/ui/Icon.vue'
import KpiCard from '@/components/ui/KpiCard.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import TabBar from '@/components/ui/TabBar.vue'
import { errorText } from '@/lib/api'
import { CATEGORY_LABEL, changeLabel, daysSince, exportUrl, type PeriodKind, shortLabel } from '@/lib/finance'
import { dateTime, shekels, title } from '@/lib/format'
import { CHANNELS } from '@/lib/orders'
import { useFinance } from '@/stores/finance'
import { useToasts } from '@/stores/toasts'
import { vCan } from '@/directives/can'

const route = useRoute()
const router = useRouter()
const finance = useFinance()
const toasts = useToasts()

onMounted(() => void finance.load())
watch(() => [finance.period, finance.month], () => void finance.load())

const TAB_IDS = ['overview', 'income', 'expenses', 'products', 'receivables', 'vat', 'settings']
const tabs = computed(() => [
  { id: 'overview', label: 'Overview' },
  { id: 'income', label: 'Income' },
  { id: 'expenses', label: 'Expenses' },
  { id: 'products', label: 'Products' },
  { id: 'receivables', label: `Receivables · ${finance.receivables.length}` },
  { id: 'vat', label: 'VAT' },
  { id: 'settings', label: 'Settings' },
])
const tab = computed({
  get: () => (TAB_IDS.includes(String(route.query.tab)) ? String(route.query.tab) : 'overview'),
  set: (t: string) => void router.replace({ query: t === 'overview' ? {} : { tab: t } }),
})
const PERIODS: { id: PeriodKind; label: string }[] = [
  { id: 'month', label: 'Month' },
  { id: 'quarter', label: 'Quarter' },
  { id: 'year', label: 'Year' },
]

const r = computed(() => finance.report)
const vs = computed(() => (r.value ? shortLabel(r.value.previous) : ''))
const oldest = computed(() => finance.receivables.reduce((n, x) => Math.max(n, daysSince(x.shipped_at) ?? 0), 0))
const kpis = computed(() => {
  const x = r.value
  if (!x) return []
  const cur = x.current
  return [
    { label: 'Net revenue', value: shekels(cur.revenue_agorot), sub: changeLabel(x.change_pct.revenue, vs.value), tone: (x.change_pct.revenue ?? 0) >= 0 ? ('ok' as const) : ('warn' as const) },
    { label: 'Expenses', value: shekels(cur.expenses_agorot), sub: changeLabel(x.change_pct.expenses, vs.value), tone: 'idle' as const },
    { label: 'Net profit', value: shekels(cur.profit_agorot), sub: changeLabel(x.change_pct.profit, vs.value), tone: (x.change_pct.profit ?? 0) >= 0 ? ('ok' as const) : ('danger' as const) },
    {
      label: 'Margin',
      value: cur.margin_pct != null ? `${cur.margin_pct}%` : '—',
      sub: x.margin_change_pt != null ? `${x.margin_change_pt >= 0 ? '▲' : '▼'} ${Math.abs(x.margin_change_pt)} pt` : '',
      tone: 'idle' as const,
    },
    {
      label: 'Receivables',
      value: shekels(x.receivables.total_agorot),
      sub: x.receivables.orders ? `${x.receivables.orders} order${x.receivables.orders === 1 ? '' : 's'}${oldest.value > 30 ? ' · over 30 days' : ''}` : 'nothing owed',
      tone: oldest.value > 30 ? ('warn' as const) : ('idle' as const),
    },
  ]
})

// ---------------------------------------------------------------- trend chart (net, ₪ thousands)
const W = 600
const H = 180
const maxTrend = computed(() => Math.max(1, ...finance.trend.flatMap((m) => [m.revenue_agorot, m.expenses_agorot])))
const minTrend = computed(() => Math.min(0, ...finance.trend.map((m) => m.profit_agorot)))
const y = (v: number) => 10 + ((maxTrend.value - v) / (maxTrend.value - minTrend.value)) * (H - 30)
const step = computed(() => W / Math.max(finance.trend.length, 1))
const profitLine = computed(() => finance.trend.map((m, i) => `${(i + 0.5) * step.value},${y(m.profit_agorot)}`).join(' '))

const mixTotal = computed(() => r.value?.current.expenses_agorot ?? 0)

// ---------------------------------------------------------------- dialogs
const money = ref<{ mode: 'payment' | 'expense'; orderId?: string } | null>(null)

// ---------------------------------------------------------------- settings
const form = reactive({ business_type: 'murshe', vat: '', tariff: '', watts: '', perGram: '', ceiling: '', labour: '', fees: {} as Record<string, string>, confirm: false })
// Channels a sale can come from (the shelf is not a sale): each may keep a % of the gross price (Wave 1 / M5).
const FEE_CHANNELS = CHANNELS.filter((c) => c !== 'stock' && c !== 'api')
watch(
  () => finance.settings,
  (s) => {
    if (!s) return
    Object.assign(form, {
      business_type: s.business_type, vat: String(Math.round(s.vat_rate * 10000) / 100), tariff: String(s.electricity_agorot_per_kwh),
      watts: String(s.default_printer_watts), perGram: String(s.default_filament_agorot_per_gram),
      ceiling: s.turnover_ceiling_agorot != null ? String(s.turnover_ceiling_agorot / 100) : '',
      labour: String((s.labour_agorot_per_hour ?? 4000) / 100),
      fees: Object.fromEntries(FEE_CHANNELS.map((c) => [c, s.channel_fees?.[c] ? String(s.channel_fees[c]) : ''])), confirm: false,
    })
  },
  { immediate: true },
)
async function saveSettings() {
  try {
    await finance.saveSettings({
      business_type: form.business_type, vat_rate: Number(form.vat) / 100, electricity_agorot_per_kwh: Number(form.tariff),
      default_printer_watts: Number(form.watts), default_filament_agorot_per_gram: Number(form.perGram),
      turnover_ceiling_agorot: form.ceiling === '' ? null : Math.round(Number(form.ceiling) * 100),
      labour_agorot_per_hour: Math.round(Number(form.labour || 0) * 100),
      channel_fees: Object.fromEntries(Object.entries(form.fees).map(([c, v]) => [c, Number(v || 0)])), confirm: form.confirm,
    })
    toasts.ok('Settings saved', 'New bookings use them; earlier entries keep the values they were booked with.')
    finance.reloadSoon()
  } catch (err) {
    toasts.error('Settings not saved', errorText(err))
  }
}
const PLACEHOLDER_LABEL: Record<string, string> = {
  vat_rate: 'VAT rate', electricity_agorot_per_kwh: 'electricity tariff', default_printer_watts: 'printer watts',
  default_filament_agorot_per_gram: 'filament price', labour_agorot_per_hour: 'labour rate',
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Finance &amp; Accounting</h1>
      <p>Income from recorded payments · expenses from POs, print jobs and manual entries</p>
    </div>
    <div class="actions">
      <a class="btn" :href="exportUrl(tab === 'expenses' ? 'expenses' : 'income', finance.period, finance.month)" download data-testid="export-csv">
        <Icon name="download" :size="14" />Export CSV
      </a>
      <button v-can="'operator'" type="button" class="btn" data-testid="record-payment" @click="money = { mode: 'payment' }"><Icon name="plus" :size="14" />Record payment</button>
      <button v-can="'operator'" type="button" class="btn btn-primary" data-testid="add-expense" @click="money = { mode: 'expense' }"><Icon name="plus" :size="14" />Add expense</button>
    </div>
  </div>

  <div class="toolbar">
    <div class="seg" role="group" aria-label="Period" data-tour="finance-period">
      <button v-for="p in PERIODS" :key="p.id" type="button" :class="{ on: finance.period === p.id }" :aria-pressed="finance.period === p.id" @click="finance.period = p.id">{{ p.label }}</button>
    </div>
    <label class="sr-only" for="fin-month">Month</label>
    <input id="fin-month" v-model="finance.month" class="input" type="month" />
    <span v-if="r" class="muted">{{ r.period.label }} vs {{ r.previous.label }}</span>
    <span v-if="finance.settings" class="chip">{{ finance.settings.business_type === 'murshe' ? 'Osek murshe' : 'Osek patur' }} · VAT {{ Math.round(finance.settings.vat_rate * 100) }}%</span>
  </div>

  <p v-if="finance.settings && !finance.settings.confirmed" class="banner" role="note" data-testid="placeholder-banner">
    <Icon name="alert" :size="16" />
    <span>
      Settings not confirmed yet: {{ finance.settings.placeholders.map((k) => PLACEHOLDER_LABEL[k]).join(', ') || 'values' }} are placeholders.
      Check them with your accountant and confirm them in <a href="?tab=settings" @click.prevent="tab = 'settings'">Settings</a>.
    </span>
  </p>
  <p v-if="finance.error" class="err" role="alert">{{ finance.error }}</p>

  <TabBar v-model="tab" :tabs="tabs" label="Finance sections" data-tour="finance-tabs" />

  <!-- ------------------------------------------------ overview -->
  <template v-if="tab === 'overview' && r">
    <div class="kpis five" data-tour="finance-kpis">
      <KpiCard v-for="k in kpis" :key="k.label" :label="k.label" :value="k.value" :sub="k.sub" :sub-tone="k.tone" />
    </div>
    <div class="grid-2">
      <section class="panel">
        <div class="panel-head"><h2>Profit · last 12 months</h2><span class="panel-note">net of VAT</span></div>
        <svg class="chart" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="Net revenue, expenses and net profit per month, last 12 months">
          <g v-for="(m, i) in finance.trend" :key="m.month">
            <rect class="rev" :x="i * step + step * 0.15" :y="y(m.revenue_agorot)" :width="step * 0.33" :height="Math.max(y(0) - y(m.revenue_agorot), 0)">
              <title>{{ m.label }} revenue {{ shekels(m.revenue_agorot) }}</title>
            </rect>
            <rect class="exp" :x="i * step + step * 0.5" :y="y(m.expenses_agorot)" :width="step * 0.33" :height="Math.max(y(0) - y(m.expenses_agorot), 0)">
              <title>{{ m.label }} expenses {{ shekels(m.expenses_agorot) }}</title>
            </rect>
            <text :x="(i + 0.5) * step" :y="H - 4" class="tick">{{ m.label.slice(0, 3) }}</text>
          </g>
          <line :x1="0" :x2="W" :y1="y(0)" :y2="y(0)" class="axis" />
          <polyline :points="profitLine" class="profit" />
        </svg>
        <div class="legend"><span><i class="sw rev" />Net revenue</span><span><i class="sw exp" />Expenses</span><span><i class="sw profit" />Net profit</span></div>
      </section>
      <section class="panel" data-testid="expense-mix">
        <div class="panel-head"><h2>Expense mix · {{ r.period.label }}</h2><span class="mono">{{ shekels(mixTotal) }}</span></div>
        <ul class="mix">
          <li v-for="(amount, cat) in r.current.mix" :key="cat">
            <span>{{ CATEGORY_LABEL[cat] ?? cat }} <span v-if="cat === 'electricity' && r.current.electricity_estimated" class="chip warn">Estimated</span></span>
            <span class="mono">{{ shekels(amount) }}</span>
            <div class="bar"><div :style="{ width: `${mixTotal ? (amount / mixTotal) * 100 : 0}%` }" /></div>
          </li>
        </ul>
        <p v-if="!Object.keys(r.current.mix).length" class="empty">No expenses in this period.</p>
        <p v-if="r.current.electricity_from_jobs && r.current.electricity_agorot" class="panel-note">
          Electricity is estimated from {{ r.print_hours }} print hours × printer watts × ₪/kWh until the bill is entered.
        </p>
      </section>
    </div>
  </template>

  <!-- ------------------------------------------------ income -->
  <section v-else-if="tab === 'income'" class="panel" data-tour="finance-income">
    <div class="panel-head"><h2>Income · {{ r?.period.label }}</h2><button v-can="'operator'" type="button" class="btn btn-sm" @click="money = { mode: 'payment' }">Record payment</button></div>
    <div class="table-wrap">
      <table class="data">
        <thead><tr><th>Date</th><th>Order</th><th>Customer</th><th>Method</th><th class="num">Gross</th><th class="num">VAT</th><th class="num">Net</th><th>Receipt no.</th><th>Source</th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in finance.income" :key="i">
            <td class="num">{{ e.entry_date }}</td>
            <td class="mono">{{ e.order_id ? `#${e.order_id.replace(/^ord-/, '')}` : '—' }}</td>
            <td>{{ e.customer_name ?? (e.order_id ? '' : 'no order') }}</td>
            <td>{{ title(e.method.replace('_', ' ')) }}</td>
            <td class="num">{{ shekels(e.gross_agorot) }}</td>
            <td class="num">{{ shekels(e.vat_agorot) }}</td>
            <td class="num">{{ shekels(e.net_agorot) }}</td>
            <td :class="{ 'text-warn': !e.receipt_no }">{{ e.receipt_no ?? 'missing' }}</td>
            <td>{{ e.source }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!finance.income.length" class="empty">No income in this period.</p>
    </div>
  </section>

  <!-- ------------------------------------------------ expenses -->
  <section v-else-if="tab === 'expenses'" class="panel" data-tour="finance-expenses">
    <div class="panel-head"><h2>Expenses · {{ r?.period.label }}</h2><button v-can="'operator'" type="button" class="btn btn-sm" @click="money = { mode: 'expense' }">Add expense</button></div>
    <div class="table-wrap">
      <table class="data">
        <thead><tr><th>Date</th><th>Category</th><th>Detail</th><th class="num">Net</th><th class="num">VAT</th><th>Kind</th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in finance.expenses" :key="i" :class="{ dim: e.kind === 'allocated' }">
            <td class="num">{{ e.entry_date }}</td>
            <td>{{ CATEGORY_LABEL[e.category] ?? e.category }}{{ e.kind === 'allocated' ? ' · per job' : '' }}</td>
            <td class="wrap">{{ [e.vendor, e.note].filter(Boolean).join(' · ') || '—' }}</td>
            <td class="num">{{ shekels(e.net_agorot) }}</td>
            <td class="num">{{ shekels(e.vat_agorot) }}</td>
            <td><StatusPill :tone="e.kind === 'cash' ? 'accent' : e.kind === 'estimated' ? 'warn' : 'idle'" :label="e.kind" /></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!finance.expenses.length" class="empty">No expenses in this period.</p>
    </div>
    <p class="panel-note">Allocated rows feed product margins only; profit and loss uses cash purchases, so filament isn’t counted twice.</p>
  </section>

  <!-- ------------------------------------------------ products -->
  <UnitEconomicsPanel v-else-if="tab === 'products'" />
  <section v-if="tab === 'products'" class="panel" data-testid="niche-margins">
    <div class="panel-head"><h2>Revenue and margin per niche · {{ r?.period.label }}</h2><span class="panel-note">each payment split across its order’s lines · adds up to the income ledger</span></div>
    <div class="table-wrap">
      <table class="data">
        <thead><tr><th>Niche</th><th class="num">Units</th><th class="num">Revenue</th><th class="num">Direct costs</th><th class="num">Margin</th><th class="num">Margin %</th><th class="num">Target</th></tr></thead>
        <tbody>
          <tr v-for="n in finance.niches" :key="n.niche" :data-testid="`niche-row-${n.niche}`">
            <td><span class="niche-dot" :style="{ background: `var(--niche-${n.color})` }" />{{ n.name }}</td>
            <td class="num">{{ n.units }}</td>
            <td class="num">{{ shekels(n.revenue_agorot) }}</td>
            <td class="num">{{ shekels((n.costs.filament ?? 0) + (n.costs.electricity ?? 0)) }}</td>
            <td class="num">{{ shekels(n.margin_agorot) }}</td>
            <td class="num" :class="{ 'text-warn': n.below_target }">{{ n.margin_pct != null ? `${n.margin_pct}%` : '—' }}<span v-if="n.below_target" class="sr-only"> (below target)</span></td>
            <td class="num muted">{{ n.target_margin_pct != null ? `${n.target_margin_pct}%` : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!finance.niches.length" class="empty">No sales or print costs in this period.</p>
    </div>
  </section>
  <section v-if="tab === 'products'" class="panel" data-testid="products">
    <NicheFilterBar what="products of this niche" />
    <div class="panel-head"><h2>Margin per product · {{ r?.period.label }}</h2><span class="panel-note">filament + electricity allocated per job from grams and print hours · catalog products per SKU</span></div>
    <div class="table-wrap">
      <table class="data">
        <thead><tr><th>Type</th><th class="num">Units</th><th class="num">Revenue</th><th class="num">Filament</th><th class="num">Electricity</th><th class="num">Margin</th><th class="num">Margin %</th><th class="num" title="Printed for stock in this period: not sold yet, so not in the margin">Built for stock</th></tr></thead>
        <tbody>
          <tr v-for="p in finance.products" :key="p.product_type">
            <td>{{ title(p.product_type) }}</td>
            <td class="num">{{ p.units }}</td>
            <td class="num">{{ shekels(p.revenue_agorot) }}</td>
            <td class="num">{{ shekels(p.costs.filament ?? 0) }}</td>
            <td class="num">{{ shekels(p.costs.electricity ?? 0) }}</td>
            <td class="num">{{ shekels(p.margin_agorot) }}</td>
            <td class="num" :class="{ 'text-warn': (p.margin_pct ?? 100) < 40 }">{{ p.margin_pct != null ? `${p.margin_pct}%` : '—' }}</td>
            <td class="num muted" :data-testid="`stock-build-${p.product_type}`">{{ p.stock_build_agorot ? shekels(p.stock_build_agorot) : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!finance.products.length" class="empty">No sales or print costs in this period.</p>
      <p v-else class="panel-note">Built for stock: filament and power spent on stock batches. They are not sold yet, so they are not taken from the margin.</p>
    </div>
  </section>

  <!-- ------------------------------------------------ receivables -->
  <section v-else-if="tab === 'receivables'" class="panel" data-testid="receivables">
    <div class="panel-head"><h2>Receivables · {{ shekels(finance.receivables.reduce((a, x) => a + x.due_agorot, 0)) }}</h2><span class="panel-note">Shipped but unpaid. Not counted as income until a payment is recorded.</span></div>
    <ul class="recv">
      <li v-for="x in finance.receivables" :key="x.order_id">
        <span class="mono">#{{ x.order_id.replace(/^ord-/, '') }}</span>
        <span>{{ x.customer_name }}</span>
        <span class="muted" :class="{ 'text-warn': (daysSince(x.shipped_at) ?? 0) > 30 }">shipped {{ daysSince(x.shipped_at) ?? '?' }} d ago</span>
        <b class="mono">{{ shekels(x.due_agorot) }}</b>
        <button v-can="'operator'" type="button" class="btn btn-sm" @click="money = { mode: 'payment', orderId: x.order_id }">Record payment</button>
      </li>
    </ul>
    <p v-if="!finance.receivables.length" class="empty">Nothing owed.</p>
  </section>

  <!-- ------------------------------------------------ VAT -->
  <section v-else-if="tab === 'vat' && finance.vat" class="panel" data-testid="vat">
    <div class="panel-head">
      <h2>VAT {{ finance.vat.year }}</h2>
      <span class="panel-note">two-month returns · output on income, input on expenses with a tax invoice</span>
    </div>
    <template v-if="finance.vat.business_type === 'murshe'">
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th>Period</th><th class="num">Output VAT</th><th class="num">Input VAT</th><th class="num">To pay</th></tr></thead>
          <tbody>
            <tr v-for="v in finance.vat.periods" :key="v.period.start" :class="{ selected: v.period.start === finance.vat.current.start }">
              <td>{{ v.period.label }}</td>
              <td class="num">{{ shekels(v.output_vat_agorot) }}</td>
              <td class="num">{{ shekels(v.input_vat_agorot) }}</td>
              <td class="num" :class="{ 'text-ok': v.payable_agorot < 0 }">{{ v.payable_agorot < 0 ? `refund ${shekels(-v.payable_agorot)}` : shekels(v.payable_agorot) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <p v-else>
      Osek patur: no VAT is charged or reclaimed. Turnover this year {{ shekels(finance.vat.turnover_gross_agorot) }}
      <template v-if="finance.vat.turnover_ceiling_agorot"> of the {{ shekels(finance.vat.turnover_ceiling_agorot) }} ceiling ({{ Math.round((finance.vat.turnover_gross_agorot / finance.vat.turnover_ceiling_agorot) * 100) }}%)</template>.
    </p>
    <p class="panel-note">A management view: file the returns from your accountant's or invoicing system's figures.</p>
  </section>

  <!-- ------------------------------------------------ settings -->
  <section v-else-if="tab === 'settings' && finance.settings" class="panel settings" data-testid="finance-settings">
    <div class="panel-head">
      <h2>Settings</h2>
      <span class="panel-note">{{ finance.settings.confirmed ? `confirmed by ${finance.settings.confirmed_by} · ${dateTime(finance.settings.confirmed_at)}` : 'placeholders · not confirmed' }}</span>
    </div>
    <form class="grid" @submit.prevent="saveSettings">
      <label>
        Business type
        <select v-model="form.business_type" class="select"><option value="murshe">Osek murshe (VAT registered)</option><option value="patur">Osek patur (no VAT)</option></select>
      </label>
      <label>VAT rate %<input v-model="form.vat" class="input" inputmode="decimal" data-testid="set-vat" /></label>
      <label>Electricity agorot per kWh<input v-model="form.tariff" class="input" inputmode="decimal" /></label>
      <label>Printer watts (average)<input v-model="form.watts" class="input" inputmode="decimal" /></label>
      <label>Filament agorot per gram (when no PO price)<input v-model="form.perGram" class="input" inputmode="decimal" /></label>
      <label>Turnover ceiling ₪ (osek patur)<input v-model="form.ceiling" class="input" inputmode="decimal" /></label>
      <label>Labour ₪ per hour (post-processing)<input v-model="form.labour" class="input" inputmode="decimal" data-testid="set-labour" /></label>
      <fieldset class="fees wide">
        <legend>Channel fees · % of the gross price the channel keeps</legend>
        <label v-for="c in FEE_CHANNELS" :key="c">{{ title(c) }}<input v-model="form.fees[c]" class="input" inputmode="decimal" placeholder="0" :data-testid="`set-fee-${c}`" /></label>
      </fieldset>
      <label class="check wide"><input v-model="form.confirm" type="checkbox" data-testid="set-confirm" /> I checked these values with my accountant</label>
      <button v-can="'owner'" type="submit" class="btn btn-primary">Save settings</button>
    </form>
    <h3>Filament prices from purchase orders</h3>
    <ul class="list">
      <li v-for="p in finance.settings.filament_prices" :key="`${p.material}${p.color}`">{{ p.material }} {{ p.color }}: {{ p.agorot_per_gram.toFixed(2) }} agorot/g net ({{ p.po_id }}, {{ dateTime(p.at) }})</li>
      <li v-if="!finance.settings.filament_prices.length" class="muted">None yet: per-job filament uses the default above until a PO is confirmed as ordered.</li>
    </ul>
  </section>

  <p class="panel-note foot">Management view. Legal receipts and tax invoices come from your invoicing system; each income line stores its receipt number.</p>

  <MoneyDialog v-if="money" :mode="money.mode" :order-id="money.orderId" :receivables="finance.receivables" @close="money = null" />
</template>

<style scoped>
.actions,
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.seg {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.seg button {
  padding: 8px 14px;
  background: transparent;
  border: 0;
  color: var(--muted);
  font: 500 13px var(--font);
  cursor: pointer;
}
.seg button.on {
  background: var(--surface-2);
  color: var(--text);
}
.toolbar .input {
  min-height: 36px;
}
.banner {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--warn);
  background: var(--warn-bg);
  color: var(--text);
  font-size: 13px;
}
.err {
  color: var(--danger);
}
.kpis.five {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
.chart {
  width: 100%;
  height: 200px;
}
.chart .rev,
.sw.rev {
  fill: var(--accent);
  background: var(--accent);
}
.chart .exp,
.sw.exp {
  fill: var(--idle);
  background: var(--idle);
}
.profit {
  fill: none;
  stroke: var(--ok);
  stroke-width: 2;
}
.sw.profit {
  background: var(--ok);
  height: 3px;
}
.axis {
  stroke: var(--border);
}
.tick {
  fill: var(--muted);
  font-size: 10px;
  text-anchor: middle;
}
.legend {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--muted);
}
.sw {
  display: inline-block;
  width: 12px;
  height: 8px;
  margin-right: 6px;
  vertical-align: middle;
  border-radius: 2px;
}
.mix {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mix li {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  font-size: 13px;
}
.mix .bar {
  grid-column: span 2;
  height: 6px;
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}
.mix .bar > div {
  height: 100%;
  background: var(--accent);
}
.data td {
  white-space: nowrap;
}
.data td.wrap {
  white-space: normal;
}
tr.dim td {
  color: var(--muted);
}
.recv {
  list-style: none;
  margin: 0;
  padding: 0;
}
.recv li {
  display: grid;
  grid-template-columns: 90px 1fr auto auto auto;
  gap: 12px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.settings .grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  align-items: end;
}
.settings label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.settings .fees {
  border: 0;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}
.settings .fees legend {
  font-size: 13px;
  margin-bottom: 6px;
}
.settings .check {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  color: var(--text);
}
.wide {
  grid-column: 1 / -1;
}
h3 {
  font: 600 13px var(--font);
}
.list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
}
.foot {
  margin-top: 4px;
}
.niche-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
}
</style>
