<script setup lang="ts">
// Niche scorecard (Wave 1 / M6): every niche side by side over a period against the one before: revenue, profit,
// profit per print hour, units and demand trend, QC failures, social leads, the products rising, the Trend
// Analyst's nightly insight, and what to do next (queue stock, raise a price, check quality).
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import DownloadMenu from '@/components/ui/DownloadMenu.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import { vCan } from '@/directives/can'
import { demandLabel, type NicheScore, type Scorecard, type Suggestion } from '@/lib/analytics'
import { api, command, errorText } from '@/lib/api'
import { ago, shekels } from '@/lib/format'
import { nicheColor } from '@/lib/niches'
import { useConfirm } from '@/stores/confirm'
import { useNiche } from '@/stores/niche'
import { useToasts } from '@/stores/toasts'

const niche = useNiche()
const confirm = useConfirm()
const toasts = useToasts()
const days = ref(30)
const card = ref<Scorecard | null>(null)
const error = ref('')
const loaded = ref(false)

async function load() {
  try {
    card.value = await api<Scorecard>(`/api/v1/analytics/scorecard?days=${days.value}`)
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loaded.value = true
  }
}
onMounted(load)
watch(days, () => void load())

// The niche in the top bar comes first (others stay visible: the point is comparing).
const niches = computed(() => {
  const all = (card.value?.niches ?? []).filter((n) => n.revenue_agorot || n.units || n.leads || n.niche !== 'unassigned')
  return niche.current ? [...all.filter((n) => n.niche === niche.current), ...all.filter((n) => n.niche !== niche.current)] : all
})
const best = computed(() => {
  const withHours = niches.value.filter((n) => n.profit_per_print_hour_agorot != null)
  return withHours.length ? withHours.reduce((a, b) => ((b.profit_per_print_hour_agorot ?? 0) > (a.profit_per_print_hour_agorot ?? 0) ? b : a)).niche : null
})
const share = (n: NicheScore) => (card.value?.revenue_agorot ? Math.round((n.revenue_agorot * 100) / card.value.revenue_agorot) : 0)

async function act(n: NicheScore, s: Suggestion) {
  if (s.kind !== 'batch' || !s.sku || !s.quantity) return
  const ok = await confirm.ask({
    title: `Queue ${s.quantity} × ${s.sku} for stock?`,
    text: `${s.sku} is rising in ${n.name}. A stock batch goes straight to the printers' queue; the pieces wait on the shelf for the next orders.`,
    confirmLabel: 'Queue batch',
  })
  if (!ok) return
  try {
    await command('catalog.batch', { items: [{ product: 'catalog', sku: s.sku, quantity: s.quantity }], notes: `scorecard: ${s.sku} rising` })
    toasts.ok('Batch queued', `${s.quantity} × ${s.sku} · follow it on the Order Pipeline`)
  } catch (err) {
    toasts.error('Batch not queued', errorText(err))
  }
}

async function rebuild() {
  const ok = await confirm.ask({ title: 'Rebuild the analytics?', text: 'The Trend Analyst recounts the last 120 days from the ledgers, then the trends. It takes a minute; the scorecard updates when it is done.', confirmLabel: 'Rebuild' })
  if (!ok) return
  try {
    await command('analytics.backfill', { days: 120 })
    toasts.info('Rebuilding the analytics', 'The Trend Analyst reports on the live feed when it is done.')
  } catch (err) {
    toasts.error('Not started', errorText(err))
  }
}
defineExpose({ load })
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Niche scorecard</h1>
      <p>Each niche side by side · the last {{ days }} days against the {{ days }} before · from the nightly analytics{{ card?.rolled_at ? ` (updated ${ago(new Date(card.rolled_at).getTime(), Date.now())} ago)` : '' }}</p>
    </div>
    <div class="head-actions">
      <label class="period">
        <span class="sr-only">Period</span>
        <select v-model.number="days" class="select" data-testid="sc-days">
          <option :value="7">7 days</option>
          <option :value="30">30 days</option>
          <option :value="90">90 days</option>
        </select>
      </label>
      <DownloadMenu :path="`/api/v1/analytics/scorecard?days=${days}`" testid="sc-download" />
      <button v-can="'owner'" type="button" class="btn btn-sm" data-testid="sc-rebuild" @click="rebuild"><Icon name="refresh" :size="14" />Rebuild</button>
    </div>
  </div>
  <p v-if="error" class="err" role="alert">{{ error }}</p>

  <div class="cards" data-tour="scorecard">
    <article v-for="n in niches" :key="n.niche" class="card" :class="{ focus: n.niche === niche.current }" :data-testid="`sc-${n.niche}`">
      <header>
        <span class="swatch" :style="{ background: nicheColor(n) }" />
        <h2>{{ n.name }}</h2>
        <span v-if="n.direction" class="pill" :class="n.direction" :data-testid="`sc-dir-${n.niche}`">{{ n.direction === 'rising' ? '▲ rising' : '▼ falling' }}</span>
        <span v-if="best === n.niche" class="pill best" title="Highest profit per print hour">best per hour</span>
      </header>
      <dl class="figures">
        <div><dt>Revenue</dt><dd :data-testid="`sc-revenue-${n.niche}`">{{ shekels(n.revenue_agorot) }}</dd><small class="muted">{{ share(n) }}% of all</small></div>
        <div><dt>Profit</dt><dd :class="{ 'text-danger': n.profit_agorot < 0 }">{{ shekels(n.profit_agorot) }}</dd>
          <small :class="n.margin_pct != null && n.target_margin_pct != null && n.margin_pct < n.target_margin_pct ? 'text-warn' : 'muted'">{{ n.margin_pct != null ? `${n.margin_pct}%` : '—' }} · target {{ n.target_margin_pct ?? '—' }}%</small></div>
        <div><dt>Per print hour</dt><dd>{{ n.profit_per_print_hour_agorot != null ? shekels(n.profit_per_print_hour_agorot) : '—' }}</dd><small class="muted">{{ n.print_hours }} h printed</small></div>
        <div><dt>Units sold</dt><dd :data-testid="`sc-units-${n.niche}`">{{ n.units }}</dd><small :class="(n.demand_pct ?? 0) < 0 ? 'text-warn' : 'muted'">{{ demandLabel(n.demand_pct, n.units) }} vs {{ n.units_prev }}</small></div>
        <div><dt>QC failed</dt><dd :class="{ 'text-warn': (n.qc_fail_pct ?? 0) >= 10 }">{{ n.qc_fail_pct != null ? `${n.qc_fail_pct}%` : '—' }}</dd><small class="muted">{{ n.qc_failed }} of {{ n.qc_checked }}</small></div>
        <div><dt>Social leads</dt><dd>{{ n.leads }}</dd><small class="muted">{{ n.leads_prev }} before</small></div>
      </dl>
      <p v-if="n.rising.length" class="small"><b>Rising:</b> <span class="mono">{{ n.rising.join(', ') }}</span></p>
      <p v-if="n.falling.length" class="small muted"><b>Cooling:</b> <span class="mono">{{ n.falling.join(', ') }}</span></p>
      <blockquote v-if="n.insight" class="insight" dir="rtl" :data-testid="`sc-insight-${n.niche}`">{{ n.insight }}</blockquote>
      <ul v-if="n.suggestions.length" class="tips" :data-testid="`sc-tips-${n.niche}`">
        <li v-for="s in n.suggestions" :key="s.kind + (s.sku ?? '')">
          <Icon :name="s.kind === 'quality' || s.kind === 'demand' ? 'alert' : s.kind === 'price' ? 'bars' : 'plus'" :size="14" />
          <span>{{ s.text }}</span>
          <button v-if="s.kind === 'batch'" v-can="'operator'" type="button" class="btn btn-sm btn-primary" :data-testid="`sc-act-${s.sku}`" @click="act(n, s)">Queue {{ s.quantity }}</button>
          <RouterLink v-else class="btn btn-sm" :to="s.to">Open</RouterLink>
        </li>
      </ul>
    </article>
  </div>
  <EmptyState v-if="loaded && card && !niches.length" icon="bars" title="No sales yet"
    text="The scorecard fills in as orders ship and payments are recorded. The Trend Analyst counts every day, once an hour." />

  <section class="panel daily">
    <div class="panel-head">
      <h2>Daily numbers</h2>
      <span class="panel-note">every day per niche, or per product · for your own spreadsheet</span>
    </div>
    <div class="row">
      <DownloadMenu :path="`/api/v1/analytics/daily?days=${days}`" label="Per niche" testid="daily-niche" />
      <DownloadMenu :path="`/api/v1/analytics/daily?days=${days}&by=sku`" label="Per product" testid="daily-sku" />
    </div>
  </section>
</template>

<style scoped>
.head-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--gap);
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--pad-panel);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.card.focus {
  border-color: var(--accent-line);
}
.card header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.card h2 {
  margin: 0;
  font-size: 15px;
}
.swatch {
  width: 14px;
  height: 14px;
  border-radius: 50%;
}
.pill {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--muted);
}
.pill.rising {
  color: var(--ok);
}
.pill.falling {
  color: var(--warn);
}
.pill.best {
  margin-left: auto;
  color: var(--accent);
}
.figures {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin: 0;
}
.figures div {
  display: flex;
  flex-direction: column;
}
dt {
  font-size: 11px;
  color: var(--muted);
}
dd {
  margin: 0;
  font: 600 16px var(--font-display);
}
small {
  font-size: 11px;
}
.insight {
  margin: 0;
  padding: 8px 12px;
  border-right: 3px solid var(--accent-line);
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.tips {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tips li {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}
.tips li > :last-child {
  margin-left: auto;
}
.daily {
  margin-top: var(--gap);
}
.daily .row {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.err {
  color: var(--danger-text);
}
@media (max-width: 420px) {
  .figures {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
