<script setup lang="ts">
// Price calculator (Wave 1 / M5): a quote for a customer in seconds. Start from a catalog product, a 3MF (a sliced
// file gives grams and print time) or typed numbers; the gateway prices it with the farm's real costs (filament,
// power, failed prints, labour, parts, channel fee) for the niche's target margin. Copy the WhatsApp text, print
// or save it as PDF, or turn it into an order.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Icon from '@/components/ui/Icon.vue'
import { vCan } from '@/directives/can'
import { api, errorText } from '@/lib/api'
import { shekels, title } from '@/lib/format'
import { CHANNELS, toAgorot } from '@/lib/orders'
import { COST_COLOR, COST_LABEL, COST_PARTS, keepQuoteForOrder, type Quote, type QuoteRequest, segments } from '@/lib/pricing'
import { useCatalog } from '@/stores/catalog'
import { useNiche } from '@/stores/niche'
import { useToasts } from '@/stores/toasts'

const router = useRouter()
const route = useRoute()
const catalog = useCatalog()
const niche = useNiche()
const toasts = useToasts()

const form = reactive({
  sku: '', title: '', grams: '', minutes: '', material: 'PLA', color: 'black', labour: '', parts: '', quantity: '1',
  extras: '', shipping: '', channel: 'whatsapp', niche: '', margin: '', price: '', days: '', customer: '',
})
const quote = ref<Quote | null>(null)
const error = ref('')
const reading = ref(false)
const sales = CHANNELS.filter((c) => c !== 'stock' && c !== 'api')
const product = computed(() => catalog.products.find((p) => p.sku === form.sku) ?? null)
// ?sku= (the command palette, a product's quote): start from that product.
if (typeof route.query.sku === 'string') form.sku = route.query.sku

onMounted(() => {
  if (!catalog.loaded) void catalog.load()
  if (!niche.loaded) void niche.load()
})

const num = (s: string) => (s.trim() === '' ? null : Number(s))
const invalid = computed(() => {
  if (!form.sku && !(Number(form.grams) > 0 && Number(form.minutes) > 0)) return 'choose a product, read a 3MF, or type the grams and print minutes'
  if (!(Number(form.quantity) >= 1)) return 'the quantity is 1 or more'
  for (const v of [form.labour, form.parts, form.extras, form.shipping, form.margin, form.price, form.days])
    if (v.trim() !== '' && !(Number(v) >= 0)) return 'numbers only'
  return null
})

function request(): QuoteRequest {
  return {
    title: form.title.trim(), sku: form.sku || null, grams: form.sku ? null : num(form.grams), print_minutes: form.sku ? null : num(form.minutes),
    material: form.material, color: form.color, labour_minutes: num(form.labour),
    parts_agorot: form.parts.trim() === '' ? null : toAgorot(form.parts), quantity: Math.floor(Number(form.quantity)),
    extras_agorot: form.extras.trim() === '' ? 0 : toAgorot(form.extras), shipping_agorot: form.shipping.trim() === '' ? 0 : toAgorot(form.shipping),
    channel: form.channel || null, niche: form.niche || null, target_margin_pct: num(form.margin),
    unit_price_agorot: form.price.trim() === '' ? null : toAgorot(form.price), days: num(form.days),
  }
}

let timer: ReturnType<typeof setTimeout> | null = null
let asked = 0
async function price() {
  if (invalid.value) {
    quote.value = null
    return
  }
  const n = ++asked
  try {
    const q = await api<Quote>('/api/v1/pricing/quote', { method: 'POST', body: request() })
    if (n === asked) {
      quote.value = q
      error.value = ''
    }
  } catch (err) {
    if (n === asked) {
      quote.value = null
      error.value = errorText(err)
    }
  }
}
watch(form, () => {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => void price(), 250)
}, { deep: true })

/** A 3MF read by the gateway: a sliced file brings the slicer's grams and print time. */
async function read3mf(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  reading.value = true
  try {
    const up = await catalog.upload(file)
    form.sku = ''
    if (up.info.est_grams != null && up.info.est_minutes != null) {
      form.grams = String(up.info.est_grams)
      form.minutes = String(up.info.est_minutes)
    } else {
      toasts.info('Not sliced', 'This 3MF has no slicer estimate: type the grams and print minutes (from your slicer).')
    }
    if (up.info.filament) form.material = up.info.filament
    if (!form.title) form.title = file.name.replace(/\.(gcode\.)?3mf$/i, '')
  } catch (err) {
    toasts.error('Could not read the file', errorText(err))
  } finally {
    reading.value = false
  }
}

async function copy() {
  if (!quote.value) return
  try {
    await navigator.clipboard.writeText(quote.value.text)
    toasts.ok('Copied', 'Paste it into WhatsApp.')
  } catch {
    toasts.error('Could not copy', 'Select the text and copy it yourself.')
  }
}
const whatsapp = computed(() => (quote.value ? `https://wa.me/?text=${encodeURIComponent(quote.value.text)}` : '#'))
const printQuote = () => window.print()

function toOrder() {
  const q = quote.value
  if (!q || !q.sku) return
  keepQuoteForOrder({ sku: q.sku, quantity: q.quantity, unit_price_agorot: q.unit_price, shipping_agorot: q.shipping_agorot,
    customer_name: form.customer.trim(), notes: `From a quote: ${q.title}${q.extras_agorot ? ` (extras ${shekels(q.extras_agorot)})` : ''}` })
  void router.push({ path: '/orders', query: { new: 'quote' } })
}
</script>

<template>
  <div class="page-head no-print">
    <div>
      <h1>Price calculator</h1>
      <p>A quote with your real costs: filament, power, failed prints, labour, parts and the channel's fee · priced for the niche's target margin</p>
    </div>
  </div>

  <div class="layout">
    <form class="panel no-print" data-testid="quote-form" data-tour="quote-form" @submit.prevent="price">
      <h2>What to price</h2>
      <label>From the catalog
        <select v-model="form.sku" class="select" data-testid="q-sku">
          <option value="">a custom print (numbers below)</option>
          <option v-for="p in catalog.products.filter((x) => x.active)" :key="p.sku" :value="p.sku">{{ p.name }} · {{ p.sku }}</option>
        </select>
      </label>
      <template v-if="!form.sku">
        <label>Read a 3MF <span class="muted small">(a sliced file gives the grams and time)</span>
          <input class="input file" type="file" accept=".3mf" data-testid="q-file" @change="read3mf" />
        </label>
        <p v-if="reading" class="muted small">Reading the file…</p>
        <div class="two">
          <label>Filament g<input v-model="form.grams" class="input" inputmode="decimal" data-testid="q-grams" /></label>
          <label>Print minutes<input v-model="form.minutes" class="input" inputmode="decimal" data-testid="q-minutes" /></label>
          <label>Material<input v-model="form.material" class="input" maxlength="20" /></label>
          <label>Colour<input v-model="form.color" class="input" maxlength="20" /></label>
          <label>Parts ₪ per piece<input v-model="form.parts" class="input" inputmode="decimal" placeholder="0" /></label>
        </div>
      </template>
      <p v-else-if="product" class="muted small">{{ product.est_grams }} g · {{ product.est_minutes }} min · catalog price {{ shekels(product.price_agorot) }}</p>
      <div class="two">
        <label>Quantity<input v-model="form.quantity" class="input" inputmode="numeric" data-testid="q-qty" /></label>
        <label>Finishing min / piece<input v-model="form.labour" class="input" inputmode="decimal" :placeholder="product ? String(product.post_minutes ?? 0) : '0'" data-testid="q-labour" /></label>
        <label>Extras ₪ (whole order)<input v-model="form.extras" class="input" inputmode="decimal" placeholder="design, rush" data-testid="q-extras" /></label>
        <label>Shipping ₪ (charged)<input v-model="form.shipping" class="input" inputmode="decimal" data-testid="q-shipping" /></label>
        <label>Sold on
          <select v-model="form.channel" class="select" data-testid="q-channel">
            <option v-for="c in sales" :key="c" :value="c">{{ title(c) }}</option>
          </select>
        </label>
        <label>Niche (target margin)
          <select v-model="form.niche" class="select">
            <option value="">{{ product ? "the product's" : 'Unassigned' }}</option>
            <option v-for="n in niche.list" :key="n.slug" :value="n.slug">{{ n.name }} · {{ n.target_margin_pct }}%</option>
          </select>
        </label>
        <label>Target margin %<input v-model="form.margin" class="input" inputmode="decimal" placeholder="niche's" data-testid="q-margin" /></label>
        <label>Or check a price ₪<input v-model="form.price" class="input" inputmode="decimal" placeholder="recommended" data-testid="q-price" /></label>
        <label>Ready in (work days)<input v-model="form.days" class="input" inputmode="numeric" /></label>
        <label>Title on the quote<input v-model="form.title" class="input" maxlength="120" :placeholder="product?.name ?? 'Custom print'" /></label>
      </div>
      <p v-if="invalid" class="muted small">{{ invalid }}</p>
      <p v-if="error" class="err" role="alert">{{ error }}</p>
    </form>

    <section class="panel result" data-testid="quote-result" data-tour="quote-result" aria-live="polite">
      <template v-if="quote">
        <div class="headline">
          <div>
            <span class="muted small">Price per piece</span>
            <b class="big" data-testid="q-unit-price">{{ shekels(quote.unit_price) }}</b>
            <span v-if="quote.recommended_price != null && quote.unit_price !== quote.recommended_price" class="muted small">recommended {{ shekels(quote.recommended_price) }}</span>
          </div>
          <div class="total">
            <span class="muted small">Total · {{ quote.quantity }} piece{{ quote.quantity === 1 ? '' : 's' }}</span>
            <b class="big" data-testid="q-total">{{ shekels(quote.total_agorot) }}</b>
          </div>
        </div>
        <p :class="quote.below_target ? 'text-warn' : 'muted'" class="small" data-testid="q-margin-line">
          Margin {{ quote.margin_pct != null ? `${quote.margin_pct}%` : '—' }} ({{ shekels(quote.margin) }} a piece, {{ shekels(quote.total_margin_agorot) }} in all){{ quote.target_margin_pct != null ? ` · target ${quote.target_margin_pct}%` : '' }}{{ quote.below_target ? ' · below target' : '' }}
          · {{ quote.print_hours }} print hours{{ quote.profit_per_print_hour != null ? ` · ${shekels(quote.profit_per_print_hour)} per print hour` : '' }}
        </p>
        <div class="bar no-print" aria-hidden="true">
          <span v-for="s in segments(quote)" :key="s.part" :style="{ width: `${s.pct}%`, background: s.part === 'margin' ? 'var(--ok)' : COST_COLOR[s.part] }" />
        </div>
        <table class="data costs">
          <tbody>
            <template v-for="p in COST_PARTS" :key="p">
              <tr v-if="(quote.costs[p] ?? 0) > 0"><td><i class="sw" :style="{ background: COST_COLOR[p] }" />{{ COST_LABEL[p] }}</td><td class="num">{{ shekels(quote.costs[p] ?? 0) }}</td></tr>
            </template>
            <tr class="sum"><td>Cost of one piece</td><td class="num">{{ shekels(quote.unit_cost) }}</td></tr>
            <tr><td>Net of VAT</td><td class="num">{{ shekels(quote.net_price) }}</td></tr>
          </tbody>
        </table>
        <p class="muted small no-print">Failed prints: {{ Math.round(quote.failure_rate * 1000) / 10 }}% of attempts lately, carried by each good piece.</p>
        <h3>The quote</h3>
        <pre class="text" data-testid="q-text">{{ quote.text }}</pre>
        <div class="actions no-print">
          <button type="button" class="btn btn-primary" data-testid="q-copy" @click="copy"><Icon name="copy" :size="14" />Copy for WhatsApp</button>
          <a class="btn" :href="whatsapp" target="_blank" rel="noopener" data-testid="q-whatsapp"><Icon name="external" :size="14" />Open WhatsApp</a>
          <button type="button" class="btn" data-testid="q-print" @click="printQuote"><Icon name="download" :size="14" />Print / save PDF</button>
          <template v-if="quote.sku">
            <input v-model="form.customer" class="input customer" placeholder="Customer name" aria-label="Customer name" data-testid="q-customer" />
            <button v-can="'operator'" type="button" class="btn" data-testid="q-order" @click="toOrder"><Icon name="plus" :size="14" />Create order</button>
          </template>
        </div>
      </template>
      <p v-else class="muted">Choose a product, read a 3MF, or type the grams and print minutes: the price appears here.</p>
    </section>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: minmax(300px, 1fr) minmax(320px, 1fr);
  gap: var(--gap);
  align-items: start;
}
form.panel,
.result {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
h2 {
  margin: 0;
  font-size: 15px;
}
h3 {
  margin: 8px 0 0;
  font-size: 14px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.file {
  padding-top: 8px;
}
.headline {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.headline div {
  display: flex;
  flex-direction: column;
}
.total {
  text-align: end;
}
.big {
  font: 700 28px var(--font-display);
}
.bar {
  display: flex;
  height: 12px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-2);
}
.costs td {
  padding: 4px 0;
}
.costs .sum td {
  font-weight: 600;
  border-top: 1px solid var(--border);
}
.sw {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-inline-end: 8px;
}
.text {
  white-space: pre-wrap;
  direction: rtl;
  text-align: end;
  font: 14px/1.6 var(--font-body, inherit);
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  padding: 12px;
  margin: 0;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.customer {
  max-width: 180px;
}
.err {
  color: var(--danger-text);
}
@media (max-width: 860px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>

<style>
/* Print / save as PDF: only the quote. */
@media print {
  .no-print,
  .sidenav,
  nav,
  header.topbar,
  .topbar,
  .toasts {
    display: none !important;
  }
  .result {
    border: 0 !important;
    box-shadow: none !important;
  }
}
</style>
