<script setup lang="ts">
// New manual order: customer, channel, due date, line items with prices, discount and shipping. The total is
// previewed here with the same rules the coordinator applies (contracts/orders.py); the coordinator decides.
import { computed, onMounted, reactive, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { shekels } from '@/lib/format'
import { CHANNELS, type Line, PRODUCTS, priceOrder, toAgorot, unitPrice } from '@/lib/orders'
import { useCatalog } from '@/stores/catalog'
import { useOrders } from '@/stores/orders'
import { useToasts } from '@/stores/toasts'

const emit = defineEmits<{ close: []; created: [orderId: string] }>()
const orders = useOrders()
const toasts = useToasts()
const catalog = useCatalog()
const skus = computed(() => catalog.products.filter((p) => p.active))

const COLORS = ['black', 'white', 'red', 'blue', 'green', 'gold', 'grey']
const form = reactive({
  customer_name: '',
  customer_ref: '',
  channel: 'manual',
  due: '',
  notes: '',
  as_lead: false,
  discount: '0',
  shipping: '0',
})
const lines = ref<(Line & { price: string })[]>([newLine()])
const busy = ref(false)
const error = ref('')
const nameInput = ref<HTMLInputElement | null>(null)

function newLine(): Line & { price: string } {
  const p = PRODUCTS[0]!
  return { product: p.key, text: '', material: 'PLA', color: 'black', quantity: 1, unit_price_agorot: null, price: (p.defaultPrice / 100).toFixed(2) }
}

function catalogLine(): Line & { price: string } {
  return { product: 'catalog', sku: '', text: '', material: 'PLA', color: 'black', quantity: 1, unit_price_agorot: null, price: '' }
}

// A catalog line takes the SKU's price (editable) and filament; the gateway fills them from the catalog again.
function pickSku(l: Line & { price: string }) {
  const p = catalog.products.find((x) => x.sku === l.sku)
  if (!p) return
  l.price = (p.price_agorot / 100).toFixed(2)
  l.material = p.material
  l.color = p.color
  l.text = p.name.slice(0, 40)
}

const asLines = computed<Line[]>(() => lines.value.map((l) => ({ ...l, unit_price_agorot: l.price === '' ? null : toAgorot(l.price) })))
const pricing = computed(() => priceOrder(asLines.value, toAgorot(form.discount || 0), toAgorot(form.shipping || 0)))
const invalid = computed(() => !form.customer_name.trim() ? 'the customer’s name is missing' : pricing.value.error)

async function submit() {
  if (invalid.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await orders.create({
      customer_name: form.customer_name.trim(),
      customer_ref: form.customer_ref.trim(),
      channel: form.channel,
      due_by: form.due ? new Date(form.due).toISOString() : null,
      notes: form.notes,
      as_lead: form.as_lead,
      discount_agorot: pricing.value.discount,
      shipping_agorot: pricing.value.shipping,
      items: asLines.value.map((l) => ({ product: l.product, ...(l.product === 'catalog' ? { sku: l.sku } : {}), text: l.text.trim(),
        material: l.material, color: l.color, quantity: l.quantity, unit_price_agorot: unitPrice(l) })),
    })
    toasts.ok(form.as_lead ? 'Lead saved' : 'Order created', `${pricing.value.units} unit(s) · ${shekels(pricing.value.total)}`)
    orders.reloadSoon()
    emit('created', String((r as { command_id: string }).command_id ?? ''))
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  nameInput.value?.focus()
  if (!catalog.loaded) void catalog.load()
})
</script>

<template>
  <div class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="new-order-title" data-testid="new-order" @submit.prevent="submit">
      <header>
        <h2 id="new-order-title">New manual order</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </header>

      <div class="grid">
        <label>Customer<input ref="nameInput" v-model="form.customer_name" class="input" maxlength="80" required data-testid="customer-name" /></label>
        <label>Phone / e-mail<input v-model="form.customer_ref" class="input" maxlength="80" /></label>
        <label>
          Channel
          <select v-model="form.channel" class="select">
            <option v-for="c in CHANNELS.filter((c) => c !== 'api')" :key="c" :value="c">{{ c }}</option>
          </select>
        </label>
        <label>Due<input v-model="form.due" class="input" type="datetime-local" /></label>
      </div>

      <h3>Items</h3>
      <div class="lines">
        <div v-for="(l, i) in lines" :key="i" class="line" :data-testid="`line-${i + 1}`">
          <label v-if="l.product === 'catalog'" class="text">
            Catalog SKU
            <select v-model="l.sku" class="select" data-testid="line-sku" @change="pickSku(l)">
              <option value="" disabled>Pick a SKU</option>
              <option v-for="p in skus" :key="p.sku" :value="p.sku">{{ p.sku }} · {{ p.name }}</option>
            </select>
          </label>
          <label v-else class="text">Text on the nameplate<input v-model="l.text" class="input" maxlength="40" dir="auto" /></label>
          <label class="qty">Qty<input v-model.number="l.quantity" class="input" type="number" min="1" max="50" /></label>
          <label v-if="l.product === 'catalog'">Filament<input class="input" :value="l.sku ? `${l.material} ${l.color}` : ''" disabled /></label>
          <label v-else>
            Color
            <select v-model="l.color" class="select">
              <option v-for="c in COLORS" :key="c" :value="c">{{ c }}</option>
            </select>
          </label>
          <label class="price">Unit ₪<input v-model="l.price" class="input" inputmode="decimal" /></label>
          <span class="total mono">{{ shekels(pricing.lines[i]) }}</span>
          <button type="button" class="icon-btn small" :aria-label="`Remove item ${i + 1}`" :disabled="lines.length === 1" @click="lines.splice(i, 1)">
            <Icon name="x" :size="14" />
          </button>
        </div>
        <div class="adds">
          <button type="button" class="btn btn-sm add" :disabled="lines.length >= 20" @click="lines.push(newLine())">
            <Icon name="plus" :size="14" />Add nameplate
          </button>
          <button v-if="skus.length" type="button" class="btn btn-sm add" :disabled="lines.length >= 20" data-testid="add-catalog-line" @click="lines.push(catalogLine())">
            <Icon name="plus" :size="14" />Add catalog item
          </button>
        </div>
      </div>

      <div class="grid">
        <label>Discount ₪<input v-model="form.discount" class="input" inputmode="decimal" /></label>
        <label>Shipping ₪<input v-model="form.shipping" class="input" inputmode="decimal" /></label>
        <label class="wide">Notes<input v-model="form.notes" class="input" maxlength="500" /></label>
      </div>

      <dl class="sum" data-testid="order-total">
        <dt>Items ({{ pricing.units }} units)</dt><dd class="mono">{{ shekels(pricing.subtotal) }}</dd>
        <dt>Discount</dt><dd class="mono">−{{ shekels(pricing.discount) }}</dd>
        <dt>Shipping</dt><dd class="mono">{{ shekels(pricing.shipping) }}</dd>
        <dt class="grand">Total (incl. VAT)</dt><dd class="mono grand">{{ shekels(pricing.total) }}</dd>
      </dl>

      <p v-if="error || invalid" class="err" role="alert">{{ error || invalid }}</p>
      <footer>
        <label class="lead"><input v-model="form.as_lead" type="checkbox" /> Save as a lead (not confirmed yet)</label>
        <button type="button" class="btn" @click="emit('close')">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!!invalid || busy">{{ form.as_lead ? 'Save lead' : 'Create order' }}</button>
      </footer>
    </form>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  z-index: 45;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  overflow-y: auto;
  padding: 40px 16px;
}
.dialog {
  width: min(820px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
header,
footer {
  display: flex;
  align-items: center;
  gap: 10px;
}
header {
  justify-content: space-between;
}
footer {
  justify-content: flex-end;
  flex-wrap: wrap;
}
h2 {
  font: 700 18px var(--font-display);
}
h3 {
  font: 600 13px var(--font);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.wide {
  grid-column: span 2;
}
.lines {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.line {
  display: grid;
  grid-template-columns: minmax(160px, 3fr) 70px minmax(90px, 1fr) 90px 90px 32px;
  gap: 8px;
  align-items: end;
}
@media (max-width: 700px) {
  .line {
    grid-template-columns: 1fr 1fr;
  }
  .wide {
    grid-column: auto;
  }
}
.total {
  padding-bottom: 10px;
  text-align: right;
  font-size: 13px;
}
.icon-btn.small {
  width: 32px;
  height: 32px;
  margin-bottom: 4px;
}
.adds {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.sum {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 16px;
  margin: 0 0 0 auto;
  min-width: 260px;
  font-size: 13px;
}
.sum dt {
  color: var(--muted);
}
.sum dd {
  margin: 0;
  text-align: right;
}
.grand {
  font-weight: 700;
  color: var(--text);
  font-size: 15px;
}
.err {
  color: var(--danger);
  font-size: 13px;
}
.lead {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  margin-right: auto;
  color: var(--text);
}
</style>
