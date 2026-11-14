// Instant Catalog: SKUs with their demand, the batch being built (kept in this browser), its estimate, uploads.
// Changes are commands; the personalization agent answers on the live feed (evt.catalog.*), which reloads this store.
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { ApiError, api, command, csrfToken, errorText, refreshSession } from '@/lib/api'
import { type Batch, type BatchEstimate, type CatalogProduct, estimateUrl, type Sort, type Upload } from '@/lib/catalog'

const BATCH_KEY = 'pf.catalog.batch'

function readBatch(): Batch {
  try {
    const raw = JSON.parse(localStorage.getItem(BATCH_KEY) ?? '{}') as unknown
    if (raw && typeof raw === 'object') {
      return Object.fromEntries(Object.entries(raw as Record<string, unknown>).filter(([, n]) => typeof n === 'number' && n > 0)) as Batch
    }
  } catch {
    /* private window or blocked storage: start empty */
  }
  return {}
}

/** POST the raw 3MF (the gateway reads the body as the file). */
async function postFile(file: Blob, name: string, retry = true): Promise<Upload> {
  const res = await fetch(`/api/v1/catalog/uploads?name=${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrfToken() },
    credentials: 'same-origin',
    body: file,
  })
  if (res.status === 401 && retry && (await refreshSession())) return postFile(file, name, false)
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as Upload
}

export const useCatalog = defineStore('catalog', () => {
  const products = ref<CatalogProduct[]>([])
  const categories = ref<string[]>([])
  const counts = ref<Record<string, number>>({})
  const category = ref<string | null>(null)
  const sort = ref<Sort>('demand')
  const showInactive = ref(false)
  const loaded = ref(false)
  const error = ref('')
  const batch = ref<Batch>(readBatch())
  const estimate = ref<BatchEstimate | null>(null)
  const estimateError = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null
  let estimating = 0

  async function load() {
    const q = new URLSearchParams({ sort: sort.value, include_inactive: String(showInactive.value) })
    if (category.value) q.set('category', category.value)
    try {
      const r = await api<{ products: CatalogProduct[]; categories: string[]; counts: Record<string, number> }>(`/api/v1/catalog/products?${q}`)
      products.value = r.products
      categories.value = r.categories
      counts.value = r.counts
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }

  function reloadSoon() {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      void load()
      void refreshEstimate()
    }, 300)
  }

  async function refreshEstimate() {
    const n = ++estimating
    if (!Object.values(batch.value).some((q) => q > 0)) {
      estimate.value = null
      estimateError.value = ''
      return
    }
    try {
      const e = await api<BatchEstimate>(estimateUrl(batch.value))
      if (n === estimating) {
        estimate.value = e
        estimateError.value = ''
      }
    } catch (err) {
      if (n === estimating) {
        estimate.value = null
        estimateError.value = errorText(err)
      }
    }
  }

  watch(
    batch,
    (b) => {
      try {
        localStorage.setItem(BATCH_KEY, JSON.stringify(b))
      } catch {
        /* not kept across reloads: fine */
      }
      void refreshEstimate()
    },
    { deep: true },
  )

  function setQty(sku: string, qty: number) {
    const next = { ...batch.value }
    if (qty > 0) next[sku] = Math.min(999, Math.floor(qty))
    else delete next[sku]
    batch.value = next
  }

  const inBatch = computed(() => Object.keys(batch.value).filter((s) => batch.value[s]! > 0))

  async function queueBatch(notes = '') {
    const items = inBatch.value.map((sku) => ({ product: 'catalog', sku, quantity: batch.value[sku] }))
    const r = await command('catalog.batch', { items, notes })
    batch.value = {}
    return r
  }

  return {
    products, categories, counts, category, sort, showInactive, loaded, error, batch, estimate, estimateError, inBatch,
    load, reloadSoon, refreshEstimate, setQty, queueBatch,
    clearBatch: () => (batch.value = {}),
    upload: (file: File) => postFile(file, file.name),
    upsert: (body: Record<string, unknown>) => command('catalog.upsert', body),
    slice: (sku: string) => command('catalog.slice', { sku }),
  }
})
