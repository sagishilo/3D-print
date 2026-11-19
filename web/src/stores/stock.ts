// Stock beyond filament (Wave 1 / M4): the shelf (finished goods), parts / packaging / consumables, and filament
// condition. Changes are commands (the coordinator owns the shelf, the inventory agent the items); their answers on
// the live feed reload this store.
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

import { api, command, errorText } from '@/lib/api'
import { withNiche } from '@/lib/niches'
import type { BomRow, Condition, FinishedGood, StockItem } from '@/lib/stock'
import { useNiche } from '@/stores/niche'

export const useStock = defineStore('stock', () => {
  const shelf = ref<FinishedGood[]>([])
  const shelfValue = ref(0)
  const items = ref<StockItem[]>([])
  const condition = ref<Condition | null>(null)
  const error = ref('')
  const loaded = ref(false)
  const allSkus = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  const niche = useNiche()

  async function load() {
    try {
      const [f, i, c] = await Promise.all([
        api<{ items: FinishedGood[]; value_agorot: number }>(withNiche(`/api/v1/inventory/finished-goods?all_skus=${allSkus.value}`, niche.current)),
        api<{ items: StockItem[] }>('/api/v1/inventory/items'),
        api<Condition>('/api/v1/inventory/condition'),
      ])
      shelf.value = f.items
      shelfValue.value = f.value_agorot
      items.value = i.items
      condition.value = c
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }
  watch([() => niche.current, allSkus], () => {
    if (loaded.value) void load()
  })

  function reloadSoon() {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), 300)
  }

  const bom = async (sku: string) => (await api<{ lines: BomRow[] }>(`/api/v1/inventory/bom/${encodeURIComponent(sku)}`)).lines

  return {
    shelf, shelfValue, items, condition, error, loaded, allSkus, load, reloadSoon, bom,
    setRule: (sku: string, minStock: number, location: string) => command('stock.rule', { sku, min_stock: minStock, location }),
    count: (sku: string, onHand: number, note = '') => command('stock.count', { sku, on_hand: onHand, note }),
    restock: (sku: string, quantity: number) =>
      command('catalog.batch', { items: [{ product: 'catalog', sku, quantity }], notes: `restock ${sku}` }),
    upsertItem: (body: Record<string, unknown>) => command('items.upsert', body),
    countItem: (itemId: string, onHand: number) => command('items.count', { item_id: itemId, on_hand: onHand }),
    reorderItem: (itemId: string, quantity?: number) => command('items.reorder', { item_id: itemId, quantity: quantity ?? null }),
    setBom: (sku: string, lines: { item_id: string; qty: number }[]) => command('bom.set', { sku, lines }),
    markDried: (spoolId: string) => command('spools.dried', { spool_id: spoolId }),
  }
})
