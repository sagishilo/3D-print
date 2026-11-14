<script setup lang="ts">
// Instant Catalog (design screen 6): ready-made SKUs from 3MF templates, ranked by 7-day demand; Import 3MF, Slice now,
// and a batch for the shelf: plates, print time and filament as the scheduler would pack them, with what is short.
// Queue batch sends one stock order (catalog.batch) that is prepared and scheduled at once.
import { computed, onMounted, ref, watch } from 'vue'

import ProductCard from '@/components/catalog/ProductCard.vue'
import ProductDialog from '@/components/catalog/ProductDialog.vue'
import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { batchLine, type CatalogProduct, CATEGORY_LABEL, estimateLine, shortages, SORTS } from '@/lib/catalog'
import { useCatalog } from '@/stores/catalog'
import { useConfirm } from '@/stores/confirm'
import { useToasts } from '@/stores/toasts'

const catalog = useCatalog()
const confirm = useConfirm()
const toasts = useToasts()

onMounted(() => {
  void catalog.load()
  void catalog.refreshEstimate()
})
watch([() => catalog.category, () => catalog.sort, () => catalog.showInactive], () => void catalog.load())

const dialog = ref<{ product: CatalogProduct | null } | null>(null)
const bed = computed<[number, number]>(() => catalog.estimate?.bed_mm ?? [256, 256])
const total = computed(() => Object.values(catalog.counts).reduce((a, b) => a + b, 0))
const est = computed(() => catalog.estimate)
const short = computed(() => (est.value ? shortages(est.value) : []))

async function slice(p: CatalogProduct) {
  try {
    await catalog.slice(p.sku)
    toasts.info(`Slicing ${p.sku}`, 'The personalization agent answers on the live feed.')
  } catch (err) {
    toasts.error('Slice failed', errorText(err))
  }
}

async function queue() {
  const e = est.value
  if (!e) return
  const warn = [
    ...short.value,
    ...(e.unsliced.length ? [`${e.unsliced.join(', ')} not sliced: real printers refuse them`] : []),
    ...(e.too_big.length ? [`${e.too_big.join(', ')} larger than the bed`] : []),
  ]
  const ok = await confirm.ask({
    title: 'Queue this batch?',
    text: `${e.pieces} piece${e.pieces === 1 ? '' : 's'} for stock: ${estimateLine(e)}. It goes straight to the printers' queue (no approval gates) and shows on the Order Pipeline as a stock order.${warn.length ? ` Note: ${warn.join('; ')}.` : ''}`,
    confirmLabel: 'Queue batch',
  })
  if (!ok) return
  try {
    const r = await catalog.queueBatch()
    toasts.ok('Batch queued', `Stock order stk-${r.command_id.slice(0, 8)} · follow it on the Order Pipeline`)
  } catch (err) {
    toasts.error('Batch not queued', errorText(err))
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>High-Demand Instant-Print Catalog</h1>
      <p>Local 3MF templates · ranked by units sold in the last 7 days · batches packed the way the scheduler packs plates</p>
    </div>
    <button type="button" class="btn btn-primary" data-testid="import-3mf" @click="dialog = { product: null }"><Icon name="plus" :size="14" />Import 3MF</button>
  </div>

  <div class="toolbar">
    <div class="cats" role="group" aria-label="Category">
      <button type="button" class="cat" :class="{ on: catalog.category === null }" :aria-pressed="catalog.category === null" @click="catalog.category = null">
        All <span class="n">{{ total }}</span>
      </button>
      <button
        v-for="c in catalog.categories.filter((c) => catalog.counts[c] || catalog.category === c)"
        :key="c"
        type="button"
        class="cat"
        :class="{ on: catalog.category === c }"
        :aria-pressed="catalog.category === c"
        @click="catalog.category = c"
      >
        {{ CATEGORY_LABEL[c] ?? c }} <span class="n">{{ catalog.counts[c] ?? 0 }}</span>
      </button>
    </div>
    <div class="right">
      <label class="small muted check"><input v-model="catalog.showInactive" type="checkbox" /> switched off too</label>
      <label class="sort">
        <span class="sr-only">Sort</span>
        <select v-model="catalog.sort" class="select" data-testid="sort">
          <option v-for="s in SORTS" :key="s.key" :value="s.key">Sort: {{ s.label }}</option>
        </select>
      </label>
    </div>
  </div>

  <p v-if="catalog.error" class="err" role="alert">{{ catalog.error }}</p>

  <div class="grid" :class="{ padded: catalog.inBatch.length }">
    <ProductCard
      v-for="p in catalog.products"
      :key="p.sku"
      :product="p"
      :qty="catalog.batch[p.sku] ?? 0"
      :bed="bed"
      @add="catalog.setQty(p.sku, $event)"
      @qty="catalog.setQty(p.sku, $event)"
      @slice="slice(p)"
      @edit="dialog = { product: p }"
    />
  </div>
  <div v-if="catalog.loaded && !catalog.products.length && !catalog.error" class="empty">
    <p>No SKUs{{ catalog.category ? ' in this category' : ' yet' }}.</p>
    <p>Import a 3MF: best a plate sliced in Bambu Studio or OrcaSlicer, so it prints as is.</p>
  </div>

  <section v-if="catalog.inBatch.length" class="batch" aria-label="Batch" data-testid="batch-bar">
    <div class="what">
      <b>Batch · {{ catalog.inBatch.length }} SKU{{ catalog.inBatch.length === 1 ? '' : 's' }}</b>
      <span class="muted mono small">{{ batchLine(catalog.batch) }}</span>
    </div>
    <div class="est" data-testid="batch-estimate">
      <span v-if="est" class="mono">{{ estimateLine(est) }}</span>
      <span v-else-if="catalog.estimateError" class="text-danger small">{{ catalog.estimateError }}</span>
      <span v-else class="muted small">estimating…</span>
      <span v-for="s in short" :key="s" class="warn-pill" data-testid="batch-short"><Icon name="alert" :size="12" />{{ s }}</span>
      <span v-if="est?.unsliced.length" class="note small" :title="est.unsliced.join(', ')">{{ est.unsliced.length }} unsliced</span>
      <span v-if="est?.too_big.length" class="text-danger small">too big for the bed: {{ est.too_big.join(', ') }}</span>
    </div>
    <div class="go">
      <button type="button" class="btn" @click="catalog.clearBatch()">Clear</button>
      <button type="button" class="btn btn-primary" :disabled="!est || !est.pieces" data-testid="queue-batch" @click="queue">Queue batch</button>
    </div>
  </section>

  <ProductDialog v-if="dialog" :product="dialog.product" :categories="catalog.categories" @close="dialog = null" />
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.cats {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.cat {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  font: 500 13px var(--font);
  cursor: pointer;
}
.cat.on {
  border-color: var(--accent);
  color: var(--text);
  background: var(--accent-bg);
}
.cat .n {
  font: 500 11px var(--font-mono);
  color: var(--muted);
}
.right {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.check {
  display: flex;
  gap: 6px;
  align-items: center;
}
.small {
  font-size: 12px;
}
.err {
  color: var(--danger);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(250px, 100%), 1fr));
  gap: 12px;
}
.grid.padded {
  padding-bottom: 110px;
}
.batch {
  position: sticky;
  bottom: 12px;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.45);
}
.what {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.what .small {
  overflow-wrap: anywhere;
}
.est {
  flex: 1;
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 13px;
}
.warn-pill {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--warn-bg);
  color: var(--warn);
  font-size: 12px;
}
.note {
  color: var(--warn);
}
.go {
  display: flex;
  gap: 8px;
}
</style>
