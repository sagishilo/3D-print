<script setup lang="ts">
// Finance > Products: the true cost of one piece of every product (filament, power, failed prints, labour, parts,
// channel fee), its margin at the catalog price, profit per print hour, and the price that reaches its niche's
// target margin. Settings (labour rate, channel fees) and each product's finishing minutes change it.
import { computed, onMounted, ref, watch } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { api, errorText } from '@/lib/api'
import { shekels, title } from '@/lib/format'
import { withNiche } from '@/lib/niches'
import { CHANNELS } from '@/lib/orders'
import { COST_COLOR, COST_LABEL, COST_PARTS, segments, type UnitEconomicsReport } from '@/lib/pricing'
import { useFinance } from '@/stores/finance'
import { useNiche } from '@/stores/niche'

const finance = useFinance()
const niche = useNiche()
const channel = ref('')
const report = ref<UnitEconomicsReport | null>(null)
const error = ref('')
const sales = CHANNELS.filter((c) => c !== 'stock' && c !== 'api')

async function load() {
  try {
    const path = channel.value ? `/api/v1/finance/unit-economics?channel=${channel.value}` : '/api/v1/finance/unit-economics'
    report.value = await api<UnitEconomicsReport>(withNiche(path, niche.current))
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  }
}
onMounted(load)
watch([channel, () => niche.current, () => finance.settings], () => void load())

const rows = computed(() => report.value?.products ?? [])
</script>

<template>
  <section class="panel" data-testid="unit-economics" data-tour="unit-economics">
    <div class="panel-head">
      <h2>Unit economics · one piece</h2>
      <span v-if="report" class="panel-note">failed prints {{ Math.round(report.failure_rate * 1000) / 10 }}% (last {{ report.failure_days }} days) · labour {{ shekels(report.labour_agorot_per_hour) }}/h</span>
      <label class="sell">
        Sold on
        <select v-model="channel" class="select" data-testid="ue-channel">
          <option value="">no channel fee</option>
          <option v-for="c in sales" :key="c" :value="c">{{ title(c) }}</option>
        </select>
      </label>
    </div>
    <p v-if="error" class="err" role="alert">{{ error }}</p>
    <p v-if="report?.below_target" class="warn-line" data-testid="ue-below"><Icon name="alert" :size="14" /> {{ report.below_target }} product{{ report.below_target === 1 ? '' : 's' }} below the niche's target margin</p>
    <div class="legend" aria-hidden="true">
      <span v-for="p in COST_PARTS" :key="p"><i :style="{ background: COST_COLOR[p] }" />{{ COST_LABEL[p] }}</span>
      <span><i class="margin" />Margin</span>
    </div>
    <div class="table-wrap">
      <table class="data">
        <thead><tr><th>Product</th><th class="num">Price</th><th class="bar-col">Where the price goes (net)</th><th class="num">Cost</th><th class="num">Margin</th><th class="num">Target</th><th class="num">₪ / print h</th><th class="num">For target</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.sku" :data-testid="`ue-${r.sku}`" :class="{ low: r.below_target }">
            <td><span class="dot" :style="{ background: `var(--niche-${r.niche_color})` }" /><b class="mono">{{ r.sku }}</b> <span class="muted">{{ r.name }}</span><span v-if="r.estimated" class="muted small"> · estimate</span></td>
            <td class="num">{{ shekels(r.gross_price) }}</td>
            <td class="bar-col">
              <div class="bar" role="img" :aria-label="segments(r).map((s) => `${s.part === 'margin' ? 'margin' : COST_LABEL[s.part]} ${shekels(s.agorot)}`).join(', ')">
                <span v-for="s in segments(r)" :key="s.part" :style="{ width: `${s.pct}%`, background: s.part === 'margin' ? 'var(--ok)' : COST_COLOR[s.part] }"
                  :title="`${s.part === 'margin' ? 'Margin' : COST_LABEL[s.part]}: ${shekels(s.agorot)}`" />
              </div>
            </td>
            <td class="num" :data-testid="`ue-cost-${r.sku}`">{{ shekels(r.unit_cost) }}</td>
            <td class="num" :class="{ 'text-warn': r.below_target, 'text-danger': r.margin < 0 }" :data-testid="`ue-margin-${r.sku}`">{{ r.margin_pct != null ? `${r.margin_pct}%` : '—' }}</td>
            <td class="num muted">{{ r.target_margin_pct != null ? `${r.target_margin_pct}%` : '—' }}</td>
            <td class="num">{{ r.profit_per_print_hour != null ? shekels(r.profit_per_print_hour) : '—' }}</td>
            <td class="num" :data-testid="`ue-rec-${r.sku}`"><b v-if="r.below_target">{{ r.recommended_price != null ? shekels(r.recommended_price) : 'no price works' }}</b><span v-else class="muted">{{ r.recommended_price != null ? shekels(r.recommended_price) : '—' }}</span></td>
          </tr>
        </tbody>
      </table>
      <p v-if="report && !rows.length" class="empty">No products on sale{{ niche.current ? ' in this niche' : '' }}.</p>
    </div>
    <p class="panel-note">Cost of one piece at the catalog's grams and print time. “For target” is the price (incl. VAT for an osek murshe, ending in 9) that reaches the niche's target margin. Change the labour rate and channel fees in Settings, a product's finishing minutes in the catalog (Edit).</p>
  </section>
</template>

<style scoped>
.panel-head {
  flex-wrap: wrap;
}
.sell {
  margin-left: auto;
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
}
.warn-line {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--warn);
  font-size: 13px;
  margin: 0 0 8px;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
.legend i {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-right: 5px;
  vertical-align: -1px;
}
.legend i.margin {
  background: var(--ok);
}
.bar-col {
  min-width: 180px;
  width: 30%;
}
.bar {
  display: flex;
  height: 12px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-2);
}
.bar span {
  height: 100%;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 8px;
}
</style>
