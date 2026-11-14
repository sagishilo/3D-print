import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ProductCard from '@/components/catalog/ProductCard.vue'
import ProductDialog from '@/components/catalog/ProductDialog.vue'
import {
  batchLine,
  type BatchEstimate,
  type CatalogProduct,
  estimateLine,
  estimateUrl,
  minutes,
  shortages,
  skuError,
  skuFromName,
  trendLabel,
} from '@/lib/catalog'
import { priceOrder } from '@/lib/orders'
import { useCatalog } from '@/stores/catalog'

import { json, on, serve } from './helpers'

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})
afterEach(() => vi.unstubAllGlobals())

const product = (over: Partial<CatalogProduct> = {}): CatalogProduct => ({
  sku: 'STD-SLAB-1E', name: 'Single slab easel', category: 'displays', material: 'PLA', color: 'black', price_agorot: 3500,
  file_name: 'easel.3mf', sliced: true, printer_model: 'C12', footprint_mm: [60, 40], est_grams: 21, est_minutes: 42,
  estimated: false, active: true, sold_7d: 52, sold_prev_7d: 38, in_production: 10, trend_pct: 36.8,
  created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', updated_by: 'admin', ...over,
})

const estimate: BatchEstimate = {
  skus: 2, pieces: 12, plates: 3, minutes: 580, grams: 640, bed_mm: [256, 256], too_big: [], unsliced: ['STD-SLAB-5T'],
  materials: [{ material: 'PLA', color: 'black', need_grams: 640, available_grams: 310, short_grams: 330 }],
}

describe('catalog helpers', () => {
  it('formats demand, time, batches and shortages like the design', () => {
    expect(minutes(190)).toBe('3 h 10 m')
    expect(minutes(42)).toBe('42 m')
    expect(trendLabel({ trend_pct: 64, sold_7d: 64 })).toBe('+64%')
    expect(trendLabel({ trend_pct: -6.2, sold_7d: 14 })).toBe('−6%')
    expect(trendLabel({ trend_pct: null, sold_7d: 3 })).toBe('new')
    expect(trendLabel({ trend_pct: null, sold_7d: 0 })).toBe('')
    const batch = { 'STD-SLAB-5T': 4, 'STD-SLAB-1E': 8, GONE: 0 }
    expect(batchLine(batch)).toBe('STD-SLAB-5T × 4 · STD-SLAB-1E × 8')
    expect(estimateUrl(batch)).toBe('/api/v1/catalog/estimate?items=STD-SLAB-5T%3A4&items=STD-SLAB-1E%3A8')
    expect(estimateLine(estimate)).toBe('3 plates · 9 h 40 m · 640 g')
    expect(shortages(estimate)).toEqual(['PLA black short by 330 g'])
    expect(skuFromName('slab easel v2.gcode.3mf')).toBe('SLAB-EASEL-V2')
    expect(skuError('STD-1')).toBeNull()
    expect(skuError('std')).toMatch(/capitals/)
  })

  it('an order needs the SKU of every catalog line', () => {
    const line = { product: 'catalog', text: '', material: 'PLA', color: 'black', quantity: 2, unit_price_agorot: 3500 }
    expect(priceOrder([line]).error).toBe('pick the SKU of every catalog item')
    expect(priceOrder([{ ...line, sku: 'STD-1' }])).toMatchObject({ error: null, total: 7000 })
  })
})

describe('ProductCard', () => {
  it('shows demand and adds what is not yet in production', async () => {
    const w = mount(ProductCard, { props: { product: product(), qty: 0, bed: [256, 256] as [number, number] } })
    expect(w.text()).toContain('52')
    expect(w.text()).toContain('+37%')
    expect(w.text()).toContain('42 m')
    expect(w.text()).toContain('21 g')
    expect(w.get('[data-testid=slice-STD-SLAB-1E]').attributes('disabled')).toBeDefined()     // already sliced
    await w.get('[data-testid=add-STD-SLAB-1E]').trigger('click')
    expect(w.emitted('add')).toEqual([[42]])                                                     // 52 sold − 10 in production
  })

  it('in the batch: a quantity, and a click takes it out again', async () => {
    const w = mount(ProductCard, { props: { product: product({ sliced: false, estimated: true }), qty: 8, bed: [256, 256] as [number, number] } })
    expect(w.text()).toContain('3MF · unsliced')
    expect(w.get('[data-testid=slice-STD-SLAB-1E]').attributes('disabled')).toBeUndefined()
    await w.get('[data-testid=in-batch-STD-SLAB-1E]').trigger('click')
    expect(w.emitted('qty')).toEqual([[0]])
  })
})

describe('catalog store', () => {
  it('estimates the batch as it changes and queues it as one stock order', async () => {
    const calls = serve(
      on('GET', /\/api\/v1\/catalog\/estimate/, () => json(200, estimate)),
      on('POST', '/api/v1/commands/catalog.batch', () => json(202, { accepted: true, command_id: 'abcdef1234567890' })),
    )
    const c = useCatalog()
    c.setQty('STD-SLAB-5T', 4)
    c.setQty('STD-SLAB-1E', 8)
    await flushPromises()
    expect(c.estimate?.plates).toBe(3)
    expect(JSON.parse(localStorage.getItem('pf.catalog.batch')!)).toEqual({ 'STD-SLAB-5T': 4, 'STD-SLAB-1E': 8 })
    await c.queueBatch()
    const post = calls.find((x) => x.method === 'POST')!
    expect(post.body).toEqual({
      items: [{ product: 'catalog', sku: 'STD-SLAB-5T', quantity: 4 }, { product: 'catalog', sku: 'STD-SLAB-1E', quantity: 8 }],
      notes: '',
    })
    await flushPromises()
    expect(c.inBatch).toEqual([])
    expect(c.estimate).toBeNull()
  })
})

describe('ProductDialog', () => {
  it('uploads the 3MF, prefills from the file and imports the SKU', async () => {
    const info = { sliced: true, plates: 1, objects: 1, footprint_mm: [60, 40], est_grams: 21, est_minutes: 42, printer_model: 'C12', filament: 'PETG', warnings: [] }
    const calls = serve(
      on('POST', /\/api\/v1\/catalog\/uploads\?name=easel\.3mf/, () => json(201, { upload: 'a'.repeat(64), name: 'easel.3mf', bytes: 2048, info })),
      on('POST', '/api/v1/commands/catalog.upsert', () => json(202, { accepted: true })),
    )
    const w = mount(ProductDialog, { props: { product: null, categories: ['displays', 'other'] } })
    const input = w.get('[data-testid=file]')
    Object.defineProperty(input.element, 'files', { value: [new File(['PK'], 'easel.3mf')] })
    await input.trigger('change')
    await flushPromises()
    expect(w.get('[data-testid=upload-info]').text()).toContain('sliced (1 plate)')
    expect((w.get('[data-testid=sku]').element as HTMLInputElement).value).toBe('EASEL')
    expect(w.find('[data-testid=grams]').exists()).toBe(false)              // the slicer's numbers are used
    await w.get('[data-testid=price]').setValue('35')
    await w.get('form').trigger('submit')
    await flushPromises()
    const upsert = calls.find((x) => x.url === '/api/v1/commands/catalog.upsert')!
    expect(upsert.body).toMatchObject({ sku: 'EASEL', name: 'easel', material: 'PETG', price_agorot: 3500, upload: 'a'.repeat(64),
      est_grams: null, est_minutes: null })
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('an unsliced template needs its grams and minutes', async () => {
    const w = mount(ProductDialog, { props: { product: product({ sliced: false, estimated: true, est_grams: 30, est_minutes: 50 }), categories: ['displays'] } })
    expect((w.get('[data-testid=grams]').element as HTMLInputElement).value).toBe('30')
    await w.get('[data-testid=grams]').setValue('')
    expect(w.get('[role=alert]').text()).toContain('enter the grams and print minutes')
    expect(w.get('[data-testid=save-product]').attributes('disabled')).toBeDefined()
  })
})
