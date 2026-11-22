// Import from link: the link is checked and kept, MakerWorld is never fetched (the 3MF gives the details), a
// Thingiverse lookup fills the form, the licence decides whether the product may go on sale (the owner's word for a
// personal-use licence), brand words are flagged, and the card links to the model page. English and Hebrew.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ProductCard from '@/components/catalog/ProductCard.vue'
import ProductDialog from '@/components/catalog/ProductDialog.vue'
import { applyLocale } from '@/i18n'
import { type CatalogProduct, licenceStatus } from '@/lib/catalog'
import { useProductActions } from '@/lib/productActions'
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
const SKU = 'PKM-TOPLOADER-STAND'
const product = (over: Partial<CatalogProduct> = {}): CatalogProduct => ({
  sku: SKU, name: 'Toploader Card Display Stand', category: 'stands', material: 'PLA', color: 'black', price_agorot: 1490,
  file_name: 'TopLoaderCardDisplay.3mf', sliced: false, printer_model: null, footprint_mm: [82, 25], est_grams: 4.2,
  est_minutes: 20, estimated: true, active: false, sold_7d: 0, sold_prev_7d: 0, in_production: 0, trend_pct: null,
  niche: 'pokemon-cards', tags: [], created_at: '2026-11-21T00:00:00Z', updated_at: '2026-11-21T00:00:00Z', updated_by: 'admin',
  designer: 'SilentYetiDesigns', licence: 'Standard Digital File License', licence_status: 'non_commercial',
  licence_label: 'Standard Digital File License (personal use)', trademarks: ['pokemon'], source_url: null, ...over,
})
const MW = { site: 'makerworld', site_name: 'MakerWorld', model_id: '111937', url: 'https://makerworld.com/en/models/111937', lookup: null,
  note: 'MakerWorld has no public API' }

describe('what a licence allows (the same rules as the server)', () => {
  it.each([
    ['Standard Digital File License', 'non_commercial'], ['BY-NC', 'non_commercial'], ['BY-NC-ND', 'non_commercial'],
    ['BY-NC-SA', 'non_commercial'], ['Creative Commons - Attribution - Non-Commercial', 'non_commercial'],
    ['MakerWorld Exclusive License', 'non_commercial'], ['BY', 'commercial'], ['BY-SA', 'commercial'], ['BY-ND', 'commercial'],
    ['CC0', 'commercial'], ['Creative Commons - Public Domain Dedication', 'commercial'], ['Creative Commons - Attribution', 'commercial'],
    ['Commercial License', 'commercial'], ['', 'unknown'], ['Some custom terms', 'unknown'],
  ])('%s → %s', (text, status) => expect(licenceStatus(text)).toBe(status))
})

describe('Import from link', () => {
  it('a MakerWorld link is checked and kept; its details come from the 3MF, which starts switched off', async () => {
    const design = { title: 'Top Loader Pokémon card Display', designer: 'SilentYetiDesigns', licence: 'Standard Digital File License',
      origin: 'original', design_id: 'US39916e4f4a1a9c', licence_status: 'non_commercial', licence_label: 'x', trademarks: ['pokemon'] }
    const info = { sliced: false, plates: 0, objects: 1, footprint_mm: [82, 25], est_grams: null, est_minutes: null, printer_model: null,
      filament: 'PLA', warnings: [], design }
    const calls = serve(
      on('GET', /\/api\/v1\/catalog\/link\?url=/, () => json(200, MW)),
      on('POST', /\/api\/v1\/catalog\/uploads/, () => json(201, { upload: 'a'.repeat(64), name: 'TopLoaderCardDisplay.3mf', bytes: 9, info })),
      on('POST', '/api/v1/commands/catalog.upsert', () => json(202, { accepted: true })),
    )
    const w = mount(ProductDialog, { props: { product: null, categories: ['stands'], linkFirst: true }, attachTo: document.body })
    expect(document.activeElement).toBe(w.get('[data-testid=link-url]').element)
    await w.get('[data-testid=link-url]').setValue('makerworld.com/en/models/111937-toploader?from=x')
    await w.get('[data-testid=link-check]').trigger('click')
    await flushPromises()
    expect(calls.find((c) => c.url.startsWith('/api/v1/catalog/link'))!.url)
      .toBe('/api/v1/catalog/link?url=makerworld.com%2Fen%2Fmodels%2F111937-toploader%3Ffrom%3Dx')
    expect(w.get('[data-testid=link-info]').text()).toContain('MakerWorld model 111937')
    expect(w.get('[data-testid=link-open]').attributes()).toMatchObject({ href: MW.url, target: '_blank', rel: 'noopener noreferrer' })
    expect(w.get('[data-testid=link-note]').text()).toContain('no public API, so the farm does not fetch its pages')
    expect((w.get('[data-testid=link-url]').element as HTMLInputElement).value).toBe(MW.url)          // canonical

    const file = w.get('[data-testid=file]')
    Object.defineProperty(file.element, 'files', { value: [new File(['PK'], 'TopLoaderCardDisplay.3mf')] })
    await file.trigger('change')
    await flushPromises()
    expect((w.get('[data-testid=designer]').element as HTMLInputElement).value).toBe('SilentYetiDesigns')
    expect((w.get('[data-testid=licence]').element as HTMLInputElement).value).toBe('Standard Digital File License')
    expect((w.get('[data-testid=name]').element as HTMLInputElement).value).toBe('Top Loader Pokémon card Display')
    expect(w.get('[data-testid=licence-verdict]').text()).toContain('Personal use only: no selling')
    expect(w.get('[data-testid=brand-warning]').text()).toContain('pokemon')
    expect(w.get('[data-testid=product-sale]').text()).toContain('Starts switched off')
    expect(w.find('[data-testid=rights]').exists()).toBe(false)                     // off: nothing to confirm
    await w.get('[data-testid=sku]').setValue('PKM-TL-2')
    await w.get('[data-testid=price]').setValue('14.90')
    await w.get('[data-testid=grams]').setValue('4.2')
    await w.get('[data-testid=minutes]').setValue('20')
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(calls.find((c) => c.url === '/api/v1/commands/catalog.upsert')!.body).toMatchObject({
      sku: 'PKM-TL-2', active: false, source_url: MW.url, designer: 'SilentYetiDesigns', licence: 'Standard Digital File License',
      rights_confirmed: false })
    w.unmount()
  })

  it('a Thingiverse link fills the form from the connector', async () => {
    serve(on('GET', /\/api\/v1\/catalog\/link/, () => json(200, { site: 'thingiverse', site_name: 'Thingiverse', model_id: '4321',
      url: 'https://www.thingiverse.com/thing:4321', note: '', lookup: { title: 'Fidget spinner', designer: 'maker', licence: 'Creative Commons - Attribution',
        image_url: '', files: ['spinner.stl'], has_3mf: false, mode: 'live', licence_status: 'commercial', licence_label: 'CC BY', trademarks: [] } })))
    const w = mount(ProductDialog, { props: { product: null, categories: ['kids'] } })
    await w.get('[data-testid=link-url]').setValue('https://www.thingiverse.com/thing:4321')
    await w.get('[data-testid=link-check]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid=link-info]').text()).toContain('No 3MF file on the page (STL only)')
    await w.get('[data-testid=link-use]').trigger('click')
    expect((w.get('[data-testid=name]').element as HTMLInputElement).value).toBe('Fidget spinner')
    expect((w.get('[data-testid=licence]').element as HTMLInputElement).value).toBe('Creative Commons - Attribution')
    expect(w.get('[data-testid=licence-verdict]').text()).toContain('Licence allows selling prints')
  })

  it('a link to anything else says why', async () => {
    serve(on('GET', /\/api\/v1\/catalog\/link/, () => json(400, { detail: 'a MakerWorld, Printables or Thingiverse model page, please' })))
    const w = mount(ProductDialog, { props: { product: null, categories: ['kids'] } })
    await w.get('[data-testid=link-url]').setValue('https://evil.example/x')
    await w.get('[data-testid=link-check]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid=link-error]').text()).toContain('MakerWorld, Printables or Thingiverse')
  })
})

describe('the right to sell', () => {
  it('switching on a personal-use product in Edit needs the confirmation, which is sent', async () => {
    const calls = serve(on('POST', '/api/v1/commands/catalog.upsert', () => json(202, { accepted: true })))
    const w = mount(ProductDialog, { props: { product: product(), categories: ['stands'] } })
    await w.get('[data-testid=product-active]').trigger('click')
    expect(w.get('[data-testid=rights]').text()).toContain('I hold the designer’s commercial licence')
    expect(w.get('[data-testid=save-product]').attributes('disabled')).toBeDefined()
    await w.get('[data-testid=rights-confirm]').setValue(true)
    expect(w.get('[data-testid=save-product]').attributes('disabled')).toBeUndefined()
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(calls.at(-1)!.body).toMatchObject({ active: true, rights_confirmed: true })
  })

  it('the switch on a card asks for it with a required tick, and sends it', async () => {
    const calls = serve(on('POST', '/api/v1/commands/catalog.active', () => json(202, { accepted: true })))
    const ask = vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    expect(await useProductActions().setActive(product(), true)).toBe(true)
    expect(ask.mock.calls[0][0]).toMatchObject({ title: `Switch ${SKU} on?`, confirmLabel: 'Switch on' })
    expect(ask.mock.calls[0][0].requireCheck).toContain('commercial licence')
    expect(calls[0].body).toEqual({ sku: SKU, active: true, rights_confirmed: true })
    ask.mockResolvedValue(false)
    expect(await useProductActions().setActive(product(), true)).toBe(false)              // not confirmed: nothing sent
    expect(calls).toHaveLength(1)
    // switching off, or a product whose licence allows selling, asks nothing
    await useProductActions().setActive(product({ active: true }), false)
    await useProductActions().setActive(product({ licence: 'BY', licence_status: 'commercial' }), true)
    expect(ask).toHaveBeenCalledTimes(2)
  })

  it('a card links to the model page and flags the licence and brand words', () => {
    const w = mount(ProductCard, { props: { product: product({ source_url: 'https://makerworld.com/en/models/1' }), qty: 0, bed: BED } })
    expect(w.get(`[data-testid=source-${SKU}]`).attributes()).toMatchObject({ href: 'https://makerworld.com/en/models/1', target: '_blank' })
    expect(w.get(`[data-testid=source-${SKU}]`).text()).toContain('SilentYetiDesigns')
    expect(w.get(`[data-testid=licence-${SKU}]`).text()).toBe('Personal use only: no selling')
    expect(w.get(`[data-testid=brand-${SKU}]`).text()).toContain('pokemon')
    const ok = mount(ProductCard, { props: { product: product({ rights_confirmed_by: 'admin' }), qty: 0, bed: BED } })
    expect(ok.get(`[data-testid=licence-${SKU}]`).text()).toBe('Commercial rights confirmed by admin')
  })

  it('in Hebrew, right to left', async () => {
    applyLocale('he')
    try {
      expect(document.documentElement.dir).toBe('rtl')
      const w = mount(ProductCard, { props: { product: product({ source_url: 'https://makerworld.com/en/models/1' }), qty: 0, bed: BED } })
      expect(w.get(`[data-testid=licence-${SKU}]`).text()).toBe('לשימוש אישי בלבד: אסור למכור')
      expect(w.get(`[data-testid=source-${SKU}]`).text()).toContain('דף המודל')
      const d = mount(ProductDialog, { props: { product: product(), categories: ['stands'] } })
      expect(d.get('[data-testid=product-link]').text()).toContain('קישור לדף המוצר')
      await d.get('[data-testid=product-active]').trigger('click')
      expect(d.get('[data-testid=rights]').text()).toContain('יש לי רישיון מסחרי מהמעצב')
    } finally {
      applyLocale('en')
    }
  })
})
