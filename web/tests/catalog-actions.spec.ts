// Catalog management: the product picture on the card and in Edit, the on-sale switch (card and Edit), delete with
// its check of what still refers to the SKU, the live feed's lines, and all of it in Hebrew, right to left.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ProductCard from '@/components/catalog/ProductCard.vue'
import ProductDialog from '@/components/catalog/ProductDialog.vue'
import { applyLocale } from '@/i18n'
import type { CatalogProduct } from '@/lib/catalog'
import { describe as describeEvent } from '@/lib/events'
import { keptBy, useProductActions } from '@/lib/productActions'
import type { Envelope } from '@/lib/types'
import { useConfirm } from '@/stores/confirm'

import { json, on, serve, signedInAs } from './helpers'

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  signedInAs()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const BED: [number, number] = [256, 256]
const SKU = 'STD-SLAB-1E'
const product = (over: Partial<CatalogProduct> = {}): CatalogProduct => ({
  sku: SKU, name: 'Single slab easel', category: 'displays', material: 'PLA', color: 'black', price_agorot: 3500,
  file_name: 'easel.3mf', sliced: true, printer_model: 'C12', footprint_mm: [60, 40], est_grams: 21, est_minutes: 42,
  estimated: false, active: true, sold_7d: 52, sold_prev_7d: 38, in_production: 10, trend_pct: 36.8, niche: 'home', tags: [],
  created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', updated_by: 'admin', ...over,
})
const env = (type: string, data: Record<string, unknown>): Envelope => ({
  id: 'x', source: '/agents/personalization', type, time: '2026-11-22T10:00:00Z', kind: 'event', correlationid: 'x', data,
})

describe('product pictures', () => {
  it('a card shows the picture, or the footprint when there is none or it fails to load', async () => {
    const w = mount(ProductCard, { props: { product: product({ image_url: `/api/v1/catalog/products/${SKU}/image?v=abc` }), qty: 0, bed: BED } })
    const img = w.get(`[data-testid=sku-image-${SKU}]`)
    expect(img.attributes('src')).toBe(`/api/v1/catalog/products/${SKU}/image?v=abc`)
    expect(img.attributes('alt')).toBe('Picture of Single slab easel')
    await img.trigger('error')
    expect(w.find(`[data-testid=sku-image-${SKU}]`).exists()).toBe(false)
    expect(w.find('svg.thumb').exists()).toBe(true)
    expect(mount(ProductCard, { props: { product: product(), qty: 0, bed: BED } }).find('img').exists()).toBe(false)
  })

  it('Edit uploads a picture at once and saves it with the product', async () => {
    const calls = serve(
      on('POST', /\/api\/v1\/catalog\/images\?name=me\.png/, () => json(201, { image_upload: 'c'.repeat(64), type: 'image/png', bytes: 8 })),
      on('POST', '/api/v1/commands/catalog.upsert', () => json(202, { accepted: true })),
    )
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:me')
    vi.spyOn(URL, 'revokeObjectURL').mockReturnValue(undefined)
    const w = mount(ProductDialog, { props: { product: product({ image_url: '/img?v=1', image_source: '3mf' }), categories: ['displays'] } })
    expect(w.get('[data-testid=product-image-source]').text()).toBe('from the 3MF preview')
    expect(w.get('[data-testid=product-image-preview]').attributes('src')).toBe('/img?v=1')
    const input = w.get('[data-testid=image-file]')
    Object.defineProperty(input.element, 'files', { value: [new File([new Uint8Array(8)], 'me.png', { type: 'image/png' })] })
    await input.trigger('change')
    await flushPromises()
    expect(w.get('[data-testid=product-image-preview]').attributes('src')).toBe('blob:me')
    expect(w.get('[data-testid=product-image-source]').text()).toBe('New picture: kept when you save')
    await w.get('[data-testid=product-active]').trigger('click')                     // switched off with the same save
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(calls.find((c) => c.url === '/api/v1/commands/catalog.upsert')!.body)
      .toMatchObject({ sku: SKU, image_upload: 'c'.repeat(64), active: false })
  })

  it('Edit refuses a picture that is not PNG, JPEG or WebP before uploading anything', async () => {
    const calls = serve()
    const w = mount(ProductDialog, { props: { product: product(), categories: ['displays'] } })
    const input = w.get('[data-testid=image-file]')
    Object.defineProperty(input.element, 'files', { value: [new File(['<svg/>'], 'x.svg', { type: 'image/svg+xml' })] })
    await input.trigger('change')
    await flushPromises()
    expect(calls.filter((c) => c.url.includes('/catalog/images'))).toEqual([])
    expect(w.get('[data-testid=product-image] [role=alert]').text()).toContain('PNG, JPEG or WebP')
    expect(w.get('[data-testid=product-image-source]').text()).toContain('No image yet')
  })
})

describe('the on-sale switch', () => {
  it('on a card: shows the state and asks to change it', async () => {
    const w = mount(ProductCard, { props: { product: product({ active: false }), qty: 0, bed: BED } })
    const sw = w.get(`[data-testid=active-${SKU}]`)
    expect(sw.attributes('role')).toBe('switch')
    expect(sw.attributes('aria-checked')).toBe('false')
    expect(sw.attributes('aria-label')).toBe(`Switch ${SKU} on: for sale again`)
    expect(w.get(`[data-testid=sale-${SKU}]`).text()).toContain('Switched off')
    await sw.trigger('click')
    expect(w.emitted('active')).toEqual([[true]])
  })

  it('a viewer sees the state but cannot change it', () => {
    signedInAs('viewer', 'dana')
    const w = mount(ProductCard, { props: { product: product(), qty: 0, bed: BED } })
    expect(w.get(`[data-testid=active-${SKU}]`).attributes('disabled')).toBeDefined()
    expect(w.get(`[data-testid=sale-${SKU}]`).text()).toContain('On sale')
  })

  it('sends catalog.active', async () => {
    const calls = serve(on('POST', '/api/v1/commands/catalog.active', () => json(202, { accepted: true })))
    expect(await useProductActions().setActive(product(), false)).toBe(true)
    expect(calls[0].body).toEqual({ sku: SKU, active: false, rights_confirmed: false })
  })
})

describe('delete', () => {
  it('names what keeps a product', () => {
    expect(keptBy({ orders: 2, stock: 1, store_maps: 3, blocked: 'x' })).toBe('2 order(s) and finished stock and 3 store listing(s)')
  })

  it('a product with orders is never deleted: the dialog offers to switch it off instead', async () => {
    const calls = serve(
      on('GET', `/api/v1/catalog/products/${SKU}/references`, () => json(200, { orders: 2, stock: 1, store_maps: 0, blocked: 'x' })),
      on('POST', '/api/v1/commands/catalog.active', () => json(202, { accepted: true })),
    )
    const ask = vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    expect(await useProductActions().remove(product())).toBe(false)
    expect(ask.mock.calls[0][0]).toMatchObject({ title: `${SKU} cannot be deleted`, confirmLabel: 'Switch off' })
    expect(ask.mock.calls[0][0].text).toContain('2 order(s) and finished stock still refer to it')
    expect(calls.map((c) => c.url)).not.toContain('/api/v1/commands/catalog.delete')
    expect(calls.find((c) => c.url === '/api/v1/commands/catalog.active')!.body).toEqual({ sku: SKU, active: false, rights_confirmed: false })
  })

  it('a product nothing refers to is deleted only after the confirmation', async () => {
    const calls = serve(
      on('GET', `/api/v1/catalog/products/${SKU}/references`, () => json(200, { orders: 0, stock: 0, store_maps: 0, blocked: null })),
      on('POST', '/api/v1/commands/catalog.delete', () => json(202, { accepted: true })),
    )
    const ask = vi.spyOn(useConfirm(), 'ask').mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    expect(await useProductActions().remove(product())).toBe(false)                    // cancelled
    expect(calls.map((c) => c.url)).not.toContain('/api/v1/commands/catalog.delete')
    expect(await useProductActions().remove(product())).toBe(true)
    expect(ask.mock.calls[1][0]).toMatchObject({ title: `Delete ${SKU}?`, danger: true, confirmLabel: 'Delete for good' })
    expect(calls.find((c) => c.url === '/api/v1/commands/catalog.delete')!.body).toEqual({ sku: SKU })
  })

  it('only owners see Delete in Edit', () => {
    expect(mount(ProductDialog, { props: { product: product(), categories: ['displays'] } }).find('[data-testid=delete-product]').exists()).toBe(true)
    signedInAs('operator', 'noa')
    expect(mount(ProductDialog, { props: { product: product(), categories: ['displays'] } }).find('[data-testid=delete-product]').exists()).toBe(false)
    expect(mount(ProductDialog, { props: { product: null, categories: ['displays'] } }).find('[data-testid=delete-product]').exists()).toBe(false)
  })

  it('the live feed says a product was deleted, or why not', () => {
    expect(describeEvent(env('io.printfarm.catalog.product.deleted.v1', { sku: 'STD-1', name: 'Easel', by: 'admin' })).text)
      .toBe('STD-1 “Easel” deleted from the catalog (admin)')
    expect(describeEvent(env('io.printfarm.catalog.product.rejected.v1', { sku: 'STD-1', action: 'delete', reason: 'it is used by 1 order', by: 'admin' })).text)
      .toBe('STD-1 not deleted · it is used by 1 order')
  })
})

describe('in Hebrew, right to left', () => {
  afterEach(() => applyLocale('en'))

  it('the switch, the picture and delete read in Hebrew', () => {
    applyLocale('he')
    expect(document.documentElement.dir).toBe('rtl')
    const w = mount(ProductCard, { props: { product: product({ image_url: '/img?v=1' }), qty: 0, bed: BED } })
    expect(w.get(`[data-testid=sale-${SKU}]`).text()).toContain('במכירה')
    expect(w.get(`[data-testid=active-${SKU}]`).attributes('aria-label')).toBe(`כיבוי ${SKU}: בלי הזמנות ואצוות חדשות`)
    expect(w.get('img').attributes('alt')).toBe('תמונה של Single slab easel')
    const d = mount(ProductDialog, { props: { product: product(), categories: ['displays'] } })
    expect(d.get('[data-testid=delete-product]').text()).toBe('מחיקה')
    expect(d.get('[data-testid=product-image-source]').text()).toContain('עדיין אין תמונה')
    expect(keptBy({ orders: 2, stock: 1, store_maps: 0, blocked: 'x' })).toBe('2 הזמנות ומלאי מוכן')
  })
})
