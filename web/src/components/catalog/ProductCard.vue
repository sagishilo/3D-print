<script setup lang="ts">
// One SKU (design screen 6): its footprint on the bed, 7-day demand, print time, filament, Slice now, the batch toggle.
import { computed } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { type CatalogProduct, CATEGORY_LABEL, minutes, trendLabel } from '@/lib/catalog'
import { shekels } from '@/lib/format'
import { vCan } from '@/directives/can'
import { nicheColor } from '@/lib/niches'
import { useNiche } from '@/stores/niche'

const props = defineProps<{ product: CatalogProduct; qty: number; bed: [number, number] }>()
const emit = defineEmits<{ add: [qty: number]; qty: [qty: number]; slice: []; edit: [] }>()

const niche = useNiche()
const trend = computed(() => trendLabel(props.product))
const trendTone = computed(() => ((props.product.trend_pct ?? 0) < 0 ? 'down' : 'up'))
/** Demand not covered by what is already being printed for stock. */
const suggested = computed(() => Math.max(1, props.product.sold_7d - props.product.in_production))
// The footprint drawn to scale on the bed (a square viewBox of the bed's larger side).
const view = computed(() => Math.max(...props.bed))
const part = computed(() => {
  const [w, d] = props.product.footprint_mm
  const [bw, bd] = props.bed
  return { x: (bw - w) / 2, y: (bd - d) / 2, w, d }
})
</script>

<template>
  <article class="card" :class="{ inactive: !product.active, picked: qty > 0 }" :data-testid="`sku-${product.sku}`" data-tour="sku-card">
    <svg class="thumb" :viewBox="`0 0 ${view} ${view}`" role="img" :aria-label="`${product.footprint_mm[0]} × ${product.footprint_mm[1]} mm on a ${bed[0]} × ${bed[1]} mm bed`">
      <rect class="bed" x="0" y="0" :width="bed[0]" :height="bed[1]" rx="6" />
      <rect class="part" :x="part.x" :y="part.y" :width="part.w" :height="part.d" rx="2" />
      <text class="dims" :x="bed[0] / 2" :y="bed[1] - 10" text-anchor="middle">{{ product.footprint_mm[0] }} × {{ product.footprint_mm[1] }} mm</text>
    </svg>

    <div class="head">
      <div class="titles">
        <b class="name">{{ product.name }}</b>
        <span class="sku mono">{{ product.sku }}</span>
        <span class="niche" :data-testid="`sku-niche-${product.sku}`"><span class="dot" :style="{ background: nicheColor(niche.bySlug[product.niche]) }" />{{ niche.name(product.niche) }}</span>
      </div>
      <span class="tag" :class="product.sliced ? 'ok' : 'warn'" :title="product.sliced ? `sliced${product.printer_model ? ` for ${product.printer_model}` : ''}` : 'model only: a real printer needs it sliced'">
        3MF · {{ product.sliced ? 'sliced' : 'unsliced' }}
      </span>
    </div>
    <span class="muted small">{{ CATEGORY_LABEL[product.category] ?? product.category }} · {{ product.material }} {{ product.color }} · {{ shekels(product.price_agorot) }}{{ product.active ? '' : ' · switched off' }}</span>

    <dl class="stats">
      <div>
        <dt>Sold 7 d</dt>
        <dd>
          <b class="mono">{{ product.sold_7d }}</b>
          <span v-if="trend" class="trend" :class="trendTone">{{ trend }}</span>
        </dd>
      </div>
      <div>
        <dt>Print</dt>
        <dd class="mono">{{ minutes(product.est_minutes) }}<span v-if="product.estimated" class="est" title="typed in, not from the slicer">~</span></dd>
      </div>
      <div>
        <dt>Filament</dt>
        <dd class="mono">{{ Math.round(product.est_grams) }} g</dd>
      </div>
    </dl>
    <span v-if="product.on_shelf" class="shelf small" :data-testid="`on-shelf-${product.sku}`">{{ product.on_shelf }} on the shelf · sold from stock first</span>
    <span v-if="product.in_production" class="muted small">{{ product.in_production }} in production for stock</span>

    <div class="actions">
      <button v-can="'operator'" type="button" class="btn btn-sm" :disabled="product.sliced" :data-testid="`slice-${product.sku}`" data-tour="sku-slice" @click="emit('slice')">
        {{ product.sliced ? 'Sliced ✓' : 'Slice now' }}
      </button>
      <button v-can="'operator'" type="button" class="btn btn-sm" :aria-label="`Edit ${product.sku}`" @click="emit('edit')"><Icon name="gear" :size="14" /></button>
      <span class="grow" />
      <template v-if="qty > 0">
        <label class="qty">
          <span class="sr-only">Pieces of {{ product.sku }} in the batch</span>
          <input class="input mono" type="number" min="0" max="999" :value="qty" :data-testid="`qty-${product.sku}`" @change="emit('qty', Number(($event.target as HTMLInputElement).value))" />
        </label>
        <button type="button" class="btn btn-sm btn-ok" :data-testid="`in-batch-${product.sku}`" title="Remove from the batch" @click="emit('qty', 0)">In batch ✓</button>
      </template>
      <button v-can="'operator'" v-else type="button" class="btn btn-sm btn-primary" :disabled="!product.active" :data-testid="`add-${product.sku}`" data-tour="sku-add" :title="`adds ${suggested}: sold this week minus stock in production`" @click="emit('add', suggested)">
        <Icon name="plus" :size="14" />Add to batch
      </button>
    </div>
  </article>
</template>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  min-width: 0;
}
.card.picked {
  border-color: var(--accent);
}
.card.inactive {
  opacity: 0.6;
}
.thumb {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: var(--surface-2, var(--bg));
  border-radius: calc(var(--radius) - 2px);
}
.bed {
  fill: none;
  stroke: var(--border);
  stroke-width: 2;
  stroke-dasharray: 6 6;
}
.part {
  fill: var(--accent);
  opacity: 0.75;
}
.dims {
  fill: var(--muted);
  font-size: 16px;
  font-family: var(--font-mono, monospace);
}
.head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: flex-start;
}
.titles {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.shelf {
  color: var(--ok);
}
.niche {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
}
.niche .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.sku {
  color: var(--muted);
  font-size: 12px;
}
.tag {
  flex-shrink: 0;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  white-space: nowrap;
}
.tag.ok {
  color: var(--ok);
  border-color: var(--ok);
}
.tag.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.small {
  font-size: 12px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin: 0;
}
.stats dt {
  font-size: 11px;
  color: var(--muted);
}
.stats dd {
  margin: 0;
  display: flex;
  gap: 6px;
  align-items: baseline;
}
.trend {
  font-size: 12px;
}
.trend.up {
  color: var(--ok);
}
.trend.down {
  color: var(--danger);
}
.est {
  color: var(--warn);
  margin-inline-start: 2px;
}
.actions {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: auto;
}
.grow {
  flex: 1;
}
.qty .input {
  width: 64px;
}
.btn-ok {
  color: var(--ok);
  border-color: var(--ok);
}
</style>
