<script setup lang="ts">
// Store orders (Wave 2 / W2-A): orders from the online stores (Shopify). The "map this" queue first (a store SKU
// that is not one of our products yet: say once what it is and every waiting order comes in), then every store
// order with what became of it, the SKU map, and the tracking sent back. Words from the i18n catalogue.
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import { api, errorText } from '@/lib/api'
import type { CatalogProduct } from '@/lib/catalog'
import { dateTime, shekels } from '@/lib/format'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { keyLabel, type Mapping, type QueueEntry, STATUSES, type StoreStatus, useStoreOrders } from '@/stores/storeOrders'
import { useToasts } from '@/stores/toasts'

const { t } = useI18n()
const store = useStoreOrders()
const auth = useAuth()
const confirm = useConfirm()
const toasts = useToasts()
const catalog = ref<CatalogProduct[]>([])

onMounted(async () => {
  void store.load()
  try {
    catalog.value = (await api<{ products: CatalogProduct[] }>('/api/v1/catalog/products?sort=name&include_inactive=false')).products
  } catch {
    catalog.value = []
  }
})

const COLORS = ['black', 'white', 'grey', 'red', 'blue', 'green', 'yellow', 'orange', 'purple', 'pink', 'gold', 'silver']
const MATERIALS = ['PLA', 'PETG', 'ABS', 'ASA', 'TPU']

// one form per queue entry, prefilled: a nameplate when customers typed a property, else a catalog SKU
const forms = reactive<Record<string, Mapping>>({})
const formFor = (q: QueueEntry): Mapping => {
  const id = `${q.connector_id}:${q.external_key}`
  if (!forms[id]) {
    const color = (q.variants[0] ?? '').toLowerCase()
    forms[id] = q.properties.length
      ? { product: 'nameplate', material: 'PLA', color: COLORS.includes(color) ? color : 'black', text_property: q.properties[0] }
      : { product: 'catalog', sku: '', material: 'PLA', color: 'black', text_property: 'Name' }
  }
  return forms[id]
}

const busy = ref('')
async function saveMap(q: QueueEntry) {
  const m = formFor(q)
  if (m.product === 'catalog' && !m.sku) {
    toasts.error(keyLabel(q.external_key), t('store.pickSku'))
    return
  }
  busy.value = q.external_key
  try {
    await store.map(q.connector_id, q.external_key, m)
    toasts.ok(t('store.mapped'), keyLabel(q.external_key))
    store.reloadSoon(800)
  } catch (err) {
    toasts.error(keyLabel(q.external_key), errorText(err))
  } finally {
    busy.value = ''
  }
}

async function unmap(connectorId: string, key: string) {
  if (!(await confirm.ask({ title: t('store.unmapAsk', { key: keyLabel(key) }), text: t('store.unmapText'), confirmLabel: t('store.unmap') }))) return
  try {
    await store.unmap(connectorId, key)
    store.reloadSoon(600)
  } catch (err) {
    toasts.error(keyLabel(key), errorText(err))
  }
}

async function act(connectorId: string, externalId: string, name: string, action: 'retry' | 'skip' | 'unskip') {
  if (action === 'skip' && !(await confirm.ask({ title: t('store.skipAsk', { name }), text: t('store.skipText'), confirmLabel: t('store.actions.skip') }))) return
  try {
    await store.act(connectorId, externalId, action)
    store.reloadSoon(600)
  } catch (err) {
    toasts.error(name, errorText(err))
  }
}

const filter = ref<StoreStatus | ''>('')
const shown = computed(() => (filter.value ? store.orders.filter((o) => o.status === filter.value) : store.orders))
const label = (connectorId: string) => store.stores.find((s) => s.connector_id === connectorId)?.label ?? connectorId
const anyOn = computed(() => store.stores.some((s) => s.enabled))
const mapLabel = (m: { product: string; sku: string; material: string; color: string; text_property: string }) =>
  m.product === 'catalog' ? t('store.asCatalog', { sku: m.sku }) : m.product === 'ignore' ? t('store.asIgnore')
    : t('store.asNameplate', { material: m.material, color: m.color, prop: m.text_property })
</script>

<template>
  <div class="page-head">
    <div>
      <h1>{{ t('store.title') }}</h1>
      <p>{{ t('store.subtitle') }}</p>
    </div>
  </div>
  <p v-if="store.error" class="err" role="alert">{{ store.error }}</p>
  <p v-if="store.loaded && !anyOn" class="note small muted" data-testid="store-off">
    <Icon name="external" :size="14" />{{ t('store.off') }}
    <RouterLink v-if="auth.can('owner')" to="/integrations">{{ t('store.connect') }}</RouterLink>
  </p>

  <section class="panel" data-tour="store-queue" data-testid="map-queue">
    <div class="panel-head"><h2>{{ t('store.queue') }}</h2><span class="panel-note">{{ t('store.queueNote') }}</span></div>
    <ul v-if="store.queue.length" class="queue">
      <li v-for="q in store.queue" :key="`${q.connector_id}:${q.external_key}`" :data-testid="`map-${q.external_key}`">
        <div class="what">
          <b class="mono">{{ keyLabel(q.external_key) }}</b>
          <span>{{ q.title }}<template v-if="q.variants.length"> · {{ q.variants.join(', ') }}</template></span>
          <span class="muted small">{{ label(q.connector_id) }} · {{ t('store.waiting', { n: q.orders.length }) }}: {{ q.orders.join(', ') }}</span>
          <span v-if="q.properties.length" class="muted small">{{ t('store.properties', { list: q.properties.join(', ') }) }}</span>
        </div>
        <form class="map-form" @submit.prevent="saveMap(q)">
          <label>{{ t('store.isA') }}
            <select v-model="formFor(q).product" class="select" :data-testid="`map-product-${q.external_key}`">
              <option value="catalog">{{ t('store.products.catalog') }}</option>
              <option value="nameplate">{{ t('store.products.nameplate') }}</option>
              <option value="ignore">{{ t('store.products.ignore') }}</option>
            </select>
          </label>
          <label v-if="formFor(q).product === 'catalog'">{{ t('store.sku') }}
            <select v-model="formFor(q).sku" class="select" :data-testid="`map-sku-${q.external_key}`">
              <option value="">{{ t('store.pickSku') }}</option>
              <option v-for="p in catalog" :key="p.sku" :value="p.sku">{{ p.sku }} · {{ p.name }}</option>
            </select>
          </label>
          <template v-if="formFor(q).product === 'nameplate'">
            <label>{{ t('store.material') }}
              <select v-model="formFor(q).material" class="select" :data-testid="`map-material-${q.external_key}`">
                <option v-for="m in MATERIALS" :key="m" :value="m">{{ m }}</option>
              </select>
            </label>
            <label>{{ t('store.color') }}
              <select v-model="formFor(q).color" class="select" :data-testid="`map-color-${q.external_key}`">
                <option v-for="c in COLORS" :key="c" :value="c">{{ c }}</option>
              </select>
            </label>
            <label>{{ t('store.textFrom') }}
              <input v-model="formFor(q).text_property" class="input" list="store-props" :data-testid="`map-prop-${q.external_key}`" />
              <datalist id="store-props"><option v-for="p in q.properties" :key="p" :value="p" /></datalist>
            </label>
          </template>
          <button type="submit" class="btn btn-sm btn-primary" :disabled="busy === q.external_key" :data-testid="`map-save-${q.external_key}`">{{ t('store.save') }}</button>
        </form>
      </li>
    </ul>
    <EmptyState v-else icon="check" :title="t('store.queueEmpty')" data-testid="queue-empty" />
  </section>

  <section class="panel" data-tour="store-orders">
    <div class="panel-head">
      <h2>{{ t('store.orders') }}</h2>
      <div class="filters" role="group" :aria-label="t('store.orders')">
        <button type="button" class="chip-btn" :class="{ on: !filter }" @click="filter = ''">{{ t('store.all') }}</button>
        <button v-for="s in STATUSES" :key="s" type="button" class="chip-btn" :class="{ on: filter === s }" :data-testid="`filter-${s}`" @click="filter = s">
          {{ t(`store.status.${s}`) }} <span class="muted">{{ store.counts[s] }}</span>
        </button>
      </div>
    </div>
    <div v-if="shown.length" class="table-wrap">
      <table class="data compact" data-testid="store-orders">
        <thead>
          <tr><th>{{ t('store.cols.order') }}</th><th>{{ t('store.cols.customer') }}</th><th class="num">{{ t('store.cols.total') }}</th><th>{{ t('store.cols.status') }}</th><th>{{ t('store.cols.ours') }}</th><th /></tr>
        </thead>
        <tbody>
          <tr v-for="o in shown" :key="`${o.connector_id}:${o.external_id}`" :data-testid="`store-order-${o.external_id}`">
            <td><b>{{ o.name }}</b><br /><span class="muted small">{{ label(o.connector_id) }} · {{ o.placed_at ? dateTime(o.placed_at) : '—' }}</span></td>
            <td>{{ o.customer || '—' }}</td>
            <td class="num">{{ o.currency === 'ILS' ? shekels(o.total_agorot) : `${(o.total_agorot / 100).toFixed(2)} ${o.currency}` }}</td>
            <td>
              <span class="st" :class="o.status" :data-testid="`status-${o.external_id}`">{{ t(`store.status.${o.status}`) }}</span>
              <ul v-if="o.problems.length" class="problems small">
                <li v-for="(p, i) in o.problems" :key="i">{{ p.kind === 'unmapped' ? t('store.unmappedLine', { key: keyLabel(p.key) }) : p.message }}</li>
              </ul>
            </td>
            <td><RouterLink v-if="o.order_id" :to="`/orders?order=${o.order_id}`" :data-testid="`ours-${o.external_id}`">{{ o.order_id }}</RouterLink><span v-else class="muted">—</span></td>
            <td class="actions">
              <button v-if="o.status === 'needs_mapping'" type="button" class="btn btn-sm" :disabled="!auth.can('operator')" @click="act(o.connector_id, o.external_id, o.name, 'retry')">{{ t('store.actions.retry') }}</button>
              <button v-if="o.status === 'needs_mapping' || o.status === 'waiting_payment'" type="button" class="btn btn-sm" :disabled="!auth.can('operator')" :data-testid="`skip-${o.external_id}`" @click="act(o.connector_id, o.external_id, o.name, 'skip')">{{ t('store.actions.skip') }}</button>
              <button v-if="o.status === 'skipped'" type="button" class="btn btn-sm" :disabled="!auth.can('operator')" :data-testid="`unskip-${o.external_id}`" @click="act(o.connector_id, o.external_id, o.name, 'unskip')">{{ t('store.actions.unskip') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-else icon="inbox" :title="t('store.none')" />
  </section>

  <div class="two">
    <section class="panel" data-tour="store-maps">
      <div class="panel-head"><h2>{{ t('store.maps') }}</h2><span class="panel-note">{{ t('store.mapsNote') }}</span></div>
      <ul v-if="store.maps.length" class="list" data-testid="sku-maps">
        <li v-for="m in store.maps" :key="`${m.connector_id}:${m.external_key}`" :data-testid="`skumap-${m.external_key}`">
          <span class="what"><span><b class="mono">{{ keyLabel(m.external_key) }}</b> → {{ mapLabel(m) }}</span>
            <span class="muted small">{{ label(m.connector_id) }} · {{ m.updated_by }}</span></span>
          <button type="button" class="btn btn-sm" :disabled="!auth.can('operator')" :data-testid="`unmap-${m.external_key}`" @click="unmap(m.connector_id, m.external_key)">{{ t('store.unmap') }}</button>
        </li>
      </ul>
      <p v-else class="muted">{{ t('store.noMaps') }}</p>
    </section>

    <section class="panel" data-tour="store-pushes">
      <div class="panel-head"><h2>{{ t('store.pushes') }}</h2><span class="panel-note">{{ t('store.pushesNote') }}</span></div>
      <ul v-if="store.pushes.length" class="list" data-testid="pushes">
        <li v-for="p in store.pushes" :key="p.id" :data-testid="`push-${p.order_id}`">
          <span><RouterLink :to="`/orders?order=${p.order_id}`">{{ p.order_id }}</RouterLink> · {{ p.payload.tracking || t('store.noTracking') }}<template v-if="p.payload.carrier"> ({{ p.payload.carrier }})</template></span>
          <span class="st" :class="p.status">{{ t(`store.push.${p.status}`) }}<template v-if="p.attempts > 1"> · {{ t('store.attempts', { n: p.attempts }) }}</template></span>
          <span v-if="p.error" class="err small">{{ p.error }}</span>
        </li>
      </ul>
      <p v-else class="muted">{{ t('store.noPushes') }}</p>
    </section>
  </div>
</template>

<style scoped>
.note {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 0 0 var(--gap);
}
.queue,
.list,
.problems {
  list-style: none;
  margin: 0;
  padding: 0;
}
.queue li {
  display: grid;
  grid-template-columns: minmax(200px, 1fr) minmax(0, 2fr);
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--border);
}
.queue li:first-child {
  border-top: 0;
}
.what {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.map-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-end;
}
.map-form label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.map-form .input {
  width: 120px;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip-btn {
  font: inherit;
  font-size: 12px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  border-radius: 999px;
  padding: 2px 10px;
  cursor: pointer;
}
.chip-btn.on {
  border-color: var(--accent);
  background: var(--surface-2);
}
.st {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}
.st.imported,
.st.sent {
  color: var(--ok);
}
.st.needs_mapping,
.st.failed {
  color: var(--danger-text);
}
.st.waiting_payment,
.st.pending {
  color: var(--warn);
}
.problems li {
  color: var(--muted);
}
.actions {
  white-space: nowrap;
  text-align: end;
}
.actions .btn + .btn {
  margin-inline-start: 6px;
}
.two {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--gap);
}
.list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 8px;
  align-items: center;
  padding: 8px 0;
  border-top: 1px solid var(--border);
}
.list li:first-child {
  border-top: 0;
}
.err {
  color: var(--danger-text);
}
@media (max-width: 760px) {
  .queue li,
  .two {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
