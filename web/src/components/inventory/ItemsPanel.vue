<script setup lang="ts">
// Inventory > Parts & packaging: what a piece takes besides filament (magnets, inserts, screws, boxes, labels).
// Each SKU's bill of materials (Edit on a product in the catalog) says how many per piece; parts are used when a
// piece is made, packaging when it leaves to a customer. Below the minimum: an alert and a reorder suggestion.
import { computed, reactive, ref } from 'vue'

import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import { vCan } from '@/directives/can'
import { errorText } from '@/lib/api'
import { shekels } from '@/lib/format'
import { toAgorot } from '@/lib/orders'
import { type ItemKind, itemIdFrom, KIND_LABEL, qty, type StockItem } from '@/lib/stock'
import { useConfirm } from '@/stores/confirm'
import { useStock } from '@/stores/stock'
import { useToasts } from '@/stores/toasts'

const stock = useStock()
const confirm = useConfirm()
const toasts = useToasts()

const editing = ref<StockItem | null>(null)
const open = ref(false)
const busy = ref(false)
const form = reactive({ id: '', name: '', kind: 'part' as ItemKind, unit: 'pcs', onHand: '', min: '0', reorder: '0', supplier: '', lead: '7', cost: '' })
const counting = ref<StockItem | null>(null)
const counted = ref('')

function edit(i: StockItem | null) {
  editing.value = i
  Object.assign(form, i
    ? { id: i.item_id, name: i.name, kind: i.kind, unit: i.unit, onHand: String(i.on_hand), min: String(i.min_qty),
        reorder: String(i.reorder_qty), supplier: i.supplier, lead: String(i.lead_days), cost: (i.cost_agorot / 100).toFixed(2) }
    : { id: '', name: '', kind: 'part', unit: 'pcs', onHand: '', min: '0', reorder: '0', supplier: '', lead: '7', cost: '' })
  open.value = true
}

const invalid = computed(() => {
  if (!form.name.trim()) return 'the name is missing'
  if (!editing.value && !/^[a-z0-9][a-z0-9-]{0,39}$/.test(form.id)) return 'the id uses a-z, 0-9 and dashes'
  if (!editing.value && stock.items.some((i) => i.item_id === form.id)) return 'an item with this id exists'
  for (const [label, v] of [['minimum', form.min], ['reorder quantity', form.reorder], ['lead days', form.lead]] as const)
    if (!(qty(v) >= 0)) return `the ${label} is a number, 0 or more`
  if (!editing.value && form.onHand !== '' && !(qty(form.onHand) >= 0)) return 'on hand is a number, 0 or more'
  if (form.cost !== '' && !(Number(form.cost) >= 0)) return 'the price is a number'
  return null
})

async function save() {
  if (invalid.value || busy.value) return
  busy.value = true
  try {
    await stock.upsertItem({
      item_id: form.id, name: form.name.trim(), item_kind: form.kind, unit: form.unit.trim() || 'pcs',
      on_hand: editing.value || form.onHand === '' ? null : qty(form.onHand), min_qty: qty(form.min),
      reorder_qty: qty(form.reorder), supplier: form.supplier.trim(), lead_days: Math.round(qty(form.lead)),
      cost_agorot: form.cost === '' ? 0 : toAgorot(form.cost),
    })
    toasts.info(`${form.name.trim()} saved`, 'The inventory agent confirms on the live feed.')
    open.value = false
    stock.reloadSoon()
  } catch (err) {
    toasts.error('Not saved', errorText(err))
  } finally {
    busy.value = false
  }
}

async function saveCount() {
  const i = counting.value
  if (!i || !(qty(counted.value) >= 0)) return
  try {
    await stock.countItem(i.item_id, qty(counted.value))
    toasts.info(`${i.name} counted`, 'The inventory agent confirms on the live feed.')
    counting.value = null
    stock.reloadSoon()
  } catch (err) {
    toasts.error('Count not saved', errorText(err))
  }
}

async function reorder(i: StockItem) {
  const n = i.suggested_qty || i.reorder_qty || 1
  const ok = await confirm.ask({
    title: `Order ${n} ${i.unit} of ${i.name}?`,
    text: `A purchase order (~${shekels(Math.round(n * i.cost_agorot))}${i.supplier ? ` from ${i.supplier}` : ''}). It shows under Filament › Purchase orders: confirm it there when you order it, and receive it when it arrives.`,
    confirmLabel: 'Create order',
  })
  if (!ok) return
  try {
    await stock.reorderItem(i.item_id, n)
    toasts.ok('Purchase order created', `${n} ${i.unit} · ${i.name}`)
  } catch (err) {
    toasts.error('Not ordered', errorText(err))
  }
}
</script>

<template>
  <section class="panel" data-testid="items" data-tour="items">
    <div class="panel-head">
      <h2>Parts, packaging and consumables</h2>
      <span class="panel-note">per piece in each product's bill of materials (catalog › Edit)</span>
      <button v-can="'operator'" type="button" class="btn btn-sm btn-primary head-btn" data-testid="add-item" @click="edit(null)"><Icon name="plus" :size="14" />Add item</button>
    </div>
    <div class="table-wrap">
      <table class="data">
        <thead><tr><th>Item</th><th>Kind</th><th class="num">On hand</th><th class="num">Minimum</th><th class="num">On order</th><th class="num">Used · 30 d</th><th class="num">Price</th><th>Used by</th><th></th></tr></thead>
        <tbody>
          <tr v-for="i in stock.items" :key="i.item_id" :data-testid="`item-${i.item_id}`" :class="{ low: i.low }">
            <td><b>{{ i.name }}</b> <span class="muted mono small">{{ i.item_id }}</span></td>
            <td>{{ KIND_LABEL[i.kind] }}</td>
            <td class="num" :class="{ 'text-warn': i.low }" :data-testid="`item-on-hand-${i.item_id}`">{{ i.on_hand }} {{ i.unit }}</td>
            <td class="num">{{ i.min_qty || '—' }}</td>
            <td class="num">{{ i.on_order || '—' }}</td>
            <td class="num">{{ i.used_30d || '—' }}</td>
            <td class="num">{{ shekels(i.cost_agorot) }}</td>
            <td class="small">{{ i.used_by.length ? i.used_by.map((u) => `${u.sku} ×${u.qty}`).join(', ') : '—' }}</td>
            <td class="actions">
              <button v-if="i.low" v-can="'operator'" type="button" class="btn btn-sm btn-primary" :data-testid="`reorder-item-${i.item_id}`" @click="reorder(i)">Reorder {{ i.suggested_qty || '' }}</button>
              <button v-can="'operator'" type="button" class="btn btn-sm" :data-testid="`count-item-${i.item_id}`" @click="counting = i; counted = String(i.on_hand)">Count</button>
              <button v-can="'operator'" type="button" class="btn btn-sm" :aria-label="`Edit ${i.name}`" @click="edit(i)">Edit</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-if="stock.loaded && !stock.items.length" icon="grid" title="No parts or packaging yet"
      text="Add the magnets, inserts, boxes and labels your products use. Then say how many each piece takes (catalog › Edit › Parts per piece): they are counted down as orders ship." />
  </section>

  <div v-if="open" class="scrim" @click.self="open = false" @keydown.esc="open = false">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="item-title" data-testid="item-dialog" @submit.prevent="save">
      <header>
        <h2 id="item-title">{{ editing ? `Edit ${editing.name}` : 'Add item' }}</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="open = false"><Icon name="x" /></button>
      </header>
      <div class="fields">
        <label>Name<input v-model="form.name" class="input" maxlength="80" data-testid="item-name" @input="!editing && (form.id = itemIdFrom(form.name))" /></label>
        <label>Id<input v-model="form.id" class="input mono" maxlength="40" :disabled="!!editing" data-testid="item-id" /></label>
        <label>Kind
          <select v-model="form.kind" class="select" data-testid="item-kind">
            <option value="part">Part (used when a piece is made)</option>
            <option value="packaging">Packaging (used when it ships)</option>
            <option value="consumable">Consumable (glue, paint: when made)</option>
          </select>
        </label>
        <label>Unit<input v-model="form.unit" class="input" maxlength="12" /></label>
        <label v-if="!editing">On hand now<input v-model="form.onHand" class="input" inputmode="decimal" data-testid="item-on-hand" /></label>
        <label>Minimum<input v-model="form.min" class="input" inputmode="decimal" data-testid="item-min" /></label>
        <label>Reorder quantity<input v-model="form.reorder" class="input" inputmode="decimal" data-testid="item-reorder" /></label>
        <label>Price per unit ₪ (net)<input v-model="form.cost" class="input" inputmode="decimal" data-testid="item-cost" /></label>
        <label>Supplier<input v-model="form.supplier" class="input" maxlength="120" /></label>
        <label>Lead days<input v-model="form.lead" class="input" inputmode="numeric" /></label>
      </div>
      <p v-if="invalid" class="muted small">{{ invalid }}</p>
      <footer>
        <button type="button" class="btn" @click="open = false">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!!invalid || busy" data-testid="save-item">Save</button>
      </footer>
    </form>
  </div>

  <div v-if="counting" class="scrim" @click.self="counting = null" @keydown.esc="counting = null">
    <form class="dialog small" role="dialog" aria-modal="true" aria-labelledby="count-title" data-testid="count-dialog" @submit.prevent="saveCount">
      <header><h2 id="count-title">Count {{ counting.name }}</h2></header>
      <label class="one">On hand ({{ counting.unit }})<input v-model="counted" class="input" inputmode="decimal" data-testid="count-value" /></label>
      <footer>
        <button type="button" class="btn" @click="counting = null">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!(qty(counted) >= 0)" data-testid="count-save">Save count</button>
      </footer>
    </form>
  </div>
</template>

<style scoped>
.panel-head {
  flex-wrap: wrap;
}
.head-btn {
  margin-left: auto;
}
.actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  display: grid;
  place-items: center;
  z-index: 50;
  padding: 16px;
}
.dialog {
  width: min(600px, 100%);
  max-height: calc(100vh - 32px);
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.dialog.small {
  width: min(360px, 100%);
}
.dialog header,
.dialog footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.dialog footer {
  justify-content: flex-end;
}
.dialog h2 {
  margin: 0;
  font-size: 16px;
}
.fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.fields label,
.one {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
@media (max-width: 560px) {
  .fields {
    grid-template-columns: 1fr;
  }
}
</style>
