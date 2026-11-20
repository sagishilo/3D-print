<script setup lang="ts">
// Inventory > Finished goods: pieces on the shelf per SKU. New orders take from the shelf first (those pieces skip
// printing); a stock batch adds to it when shipped to the shelf. Set a minimum to get a restock suggestion.
import { computed, reactive, ref } from 'vue'

import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import { vCan } from '@/directives/can'
import { errorText } from '@/lib/api'
import { shekels } from '@/lib/format'
import { nicheColor } from '@/lib/niches'
import type { FinishedGood } from '@/lib/stock'
import { useConfirm } from '@/stores/confirm'
import { useNiche } from '@/stores/niche'
import { useStock } from '@/stores/stock'
import { useToasts } from '@/stores/toasts'

const stock = useStock()
const niche = useNiche()
const confirm = useConfirm()
const toasts = useToasts()

const low = computed(() => stock.shelf.filter((s) => s.low))
const editing = ref<FinishedGood | null>(null)
const form = reactive({ min: '0', location: '', count: '', note: '' })
const busy = ref(false)

function edit(s: FinishedGood) {
  editing.value = s
  Object.assign(form, { min: String(s.min_stock), location: s.location, count: '', note: '' })
}

async function save() {
  const s = editing.value
  if (!s || busy.value) return
  const min = Number(form.min)
  const count = form.count.trim() === '' ? null : Number(form.count)
  if (!(Number.isInteger(min) && min >= 0) || (count !== null && !(Number.isInteger(count) && count >= 0))) {
    toasts.error('Not saved', 'The minimum and the count are whole numbers, 0 or more.')
    return
  }
  busy.value = true
  try {
    if (min !== s.min_stock || form.location.trim() !== s.location) await stock.setRule(s.sku, min, form.location.trim())
    if (count !== null) await stock.count(s.sku, count, form.note.trim())
    toasts.info(`${s.sku} saved`, 'The shelf updates when the coordinator confirms.')
    editing.value = null
    stock.reloadSoon()
  } catch (err) {
    toasts.error('Not saved', errorText(err))
  } finally {
    busy.value = false
  }
}

async function restock(s: FinishedGood) {
  const n = s.restock || Math.max(1, s.min_stock)
  const ok = await confirm.ask({
    title: `Queue a restock batch of ${s.sku}?`,
    text: `${n} piece${n === 1 ? '' : 's'} for the shelf (${s.free} free, minimum ${s.min_stock}${s.in_production ? `, ${s.in_production} already being made` : ''}). It goes straight to the printers' queue and shows on the Order Pipeline as a stock order.`,
    confirmLabel: 'Queue restock',
  })
  if (!ok) return
  try {
    await stock.restock(s.sku, n)
    toasts.ok('Restock queued', `${n} × ${s.sku} · follow it on the Order Pipeline`)
    stock.reloadSoon()
  } catch (err) {
    toasts.error('Restock not queued', errorText(err))
  }
}
</script>

<template>
  <section class="panel" data-testid="shelf" data-tour="shelf">
    <div class="panel-head">
      <h2>Finished goods on the shelf</h2>
      <span class="panel-note">value {{ shekels(stock.shelfValue) }} at build cost · new orders take from here first</span>
      <label class="small muted check"><input v-model="stock.allSkus" type="checkbox" data-testid="shelf-all" /> every SKU</label>
    </div>
    <p v-if="low.length" class="warn-line" data-testid="shelf-low"><Icon name="alert" :size="14" /> Below the minimum: {{ low.map((s) => s.sku).join(', ') }}</p>
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr><th>SKU</th><th class="num">On shelf</th><th class="num">Reserved</th><th class="num">Free</th><th class="num">Minimum</th><th class="num">Being made</th><th>Location</th><th class="num">Cost / piece</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in stock.shelf" :key="s.sku" :data-testid="`shelf-${s.sku}`" :class="{ low: s.low }">
            <td><span class="dot" :style="{ background: nicheColor(niche.bySlug[s.niche]) }" /><b class="mono">{{ s.sku }}</b> <span class="muted">{{ s.name }}</span></td>
            <td class="num">{{ s.on_hand }}</td>
            <td class="num">{{ s.reserved }}</td>
            <td class="num" :class="{ 'text-warn': s.low }" :data-testid="`shelf-free-${s.sku}`">{{ s.free }}</td>
            <td class="num">{{ s.min_stock || '—' }}</td>
            <td class="num">{{ s.in_production || '—' }}</td>
            <td>{{ s.location || '—' }}</td>
            <td class="num">{{ s.unit_cost_agorot != null ? shekels(s.unit_cost_agorot) : '—' }}</td>
            <td class="actions">
              <button v-if="s.low || s.restock" v-can="'operator'" type="button" class="btn btn-sm btn-primary" :data-testid="`restock-${s.sku}`" @click="restock(s)">Restock {{ s.restock || '' }}</button>
              <button v-can="'operator'" type="button" class="btn btn-sm" :data-testid="`shelf-edit-${s.sku}`" @click="edit(s)">Edit</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-if="stock.loaded && !stock.shelf.length" icon="grid" title="Nothing on the shelf yet"
      text="Queue a batch for stock in the Instant Catalog: when it ships to the shelf, its pieces show here, and new orders take them first. Tick “every SKU” to set minimums before that." />
  </section>

  <div v-if="editing" class="scrim" @click.self="editing = null" @keydown.esc="editing = null">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="shelf-title" data-testid="shelf-dialog" @submit.prevent="save">
      <header>
        <h2 id="shelf-title">{{ editing.sku }} on the shelf</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="editing = null"><Icon name="x" /></button>
      </header>
      <div class="fields">
        <label>Minimum to keep<input v-model="form.min" class="input" inputmode="numeric" data-testid="shelf-min" /></label>
        <label>Location<input v-model="form.location" class="input" maxlength="40" placeholder="Shelf B2" /></label>
        <label>Counted on the shelf <span class="muted small">(optional: corrects the number)</span><input v-model="form.count" class="input" inputmode="numeric" :placeholder="String(editing.on_hand)" data-testid="shelf-count" /></label>
        <label>Note<input v-model="form.note" class="input" maxlength="200" placeholder="monthly count" /></label>
      </div>
      <p class="muted small">{{ editing.reserved }} piece{{ editing.reserved === 1 ? ' is' : 's are' }} promised to open orders: a count never goes below that.</p>
      <footer>
        <button type="button" class="btn" @click="editing = null">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="busy" data-testid="shelf-save">Save</button>
      </footer>
    </form>
  </div>
</template>

<style scoped>
.panel-head {
  flex-wrap: wrap;
}
.check {
  margin-inline-start: auto;
  display: flex;
  gap: 6px;
  align-items: center;
}
.warn-line {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--warn);
  font-size: 13px;
  margin: 0 0 8px;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-inline-end: 8px;
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
  width: min(520px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
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
.fields label {
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
