// Store orders (Wave 2 / W2-A): what the online stores (Shopify) sent, the "map this" queue, the SKU map and the
// tracking sent back (gateway/app/stores.py). Changes are commands to the integrations agent; its events reload this.
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, command, errorText } from '@/lib/api'

export type StoreStatus = 'needs_mapping' | 'waiting_payment' | 'imported' | 'cancelled' | 'skipped'
export type StoreProduct = 'catalog' | 'nameplate' | 'ignore'

export interface StoreProblem {
  kind: 'unmapped' | 'problem'
  key: string
  title: string
  message: string
}

export interface StoreOrderRow {
  connector_id: string
  external_id: string
  name: string
  customer: string
  total_agorot: number
  currency: string
  financial_status: string
  placed_at: string | null
  status: StoreStatus
  order_id: string | null
  problems: StoreProblem[]
  seen_at: string
  imported_at: string | null
}

export interface QueueEntry {
  connector_id: string
  external_key: string
  title: string
  orders: string[]
  properties: string[]
  variants: string[]
}

export interface SkuMapRow {
  connector_id: string
  external_key: string
  product: StoreProduct
  sku: string
  material: string
  color: string
  text_property: string
  updated_by: string
  updated_at: string
}

export interface PushRow {
  id: number
  connector_id: string
  kind: string
  external_id: string
  order_id: string
  payload: { carrier?: string; tracking?: string; url?: string }
  status: 'pending' | 'sent' | 'failed' | 'skipped'
  attempts: number
  error: string | null
  created_at: string
  sent_at: string | null
}

export interface StoreInfo {
  connector_id: string
  label: string
  enabled: boolean
  mode: 'fake' | 'live'
}

export interface Mapping {
  product: StoreProduct
  sku?: string
  material?: string
  color?: string
  text_property?: string
}

export const STATUSES: StoreStatus[] = ['needs_mapping', 'waiting_payment', 'imported', 'cancelled', 'skipped']

/** The store's SKU as people read it: "title:Gift wrap" (a product without a SKU) shows as its title. */
export const keyLabel = (key: string) => (key.startsWith('title:') ? key.slice(6) : key)

export const useStoreOrders = defineStore('storeOrders', () => {
  const orders = ref<StoreOrderRow[]>([])
  const counts = ref<Record<StoreStatus, number>>({ needs_mapping: 0, waiting_payment: 0, imported: 0, cancelled: 0, skipped: 0 })
  const queue = ref<QueueEntry[]>([])
  const stores = ref<StoreInfo[]>([])
  const maps = ref<SkuMapRow[]>([])
  const pushes = ref<PushRow[]>([])
  const error = ref('')
  const loaded = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  async function load() {
    try {
      const [o, m, p] = await Promise.all([
        api<{ orders: StoreOrderRow[]; counts: Record<StoreStatus, number>; queue: QueueEntry[]; stores: StoreInfo[] }>('/api/v1/store/orders'),
        api<{ maps: SkuMapRow[] }>('/api/v1/store/maps'),
        api<{ pushes: PushRow[] }>('/api/v1/store/pushes'),
      ])
      orders.value = o.orders
      counts.value = o.counts
      queue.value = o.queue
      stores.value = o.stores
      maps.value = m.maps
      pushes.value = p.pushes
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }

  function reloadSoon(ms = 400) {
    if (!loaded.value) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), ms)
  }

  function map(connectorId: string, externalKey: string, m: Mapping) {
    const body: Record<string, unknown> = { connector_id: connectorId, external_key: externalKey, product: m.product }
    if (m.product === 'catalog') body.sku = m.sku
    if (m.product === 'nameplate') Object.assign(body, { material: m.material, color: m.color, text_property: m.text_property })
    return command('integrations.map_sku', body)
  }

  return {
    orders, counts, queue, stores, maps, pushes, error, loaded, load, reloadSoon, map,
    unmap: (connectorId: string, externalKey: string) =>
      command('integrations.map_sku', { connector_id: connectorId, external_key: externalKey, remove: true }),
    act: (connectorId: string, externalId: string, action: 'retry' | 'skip' | 'unskip') =>
      command('integrations.store_order', { connector_id: connectorId, external_id: externalId, action }),
  }
})
