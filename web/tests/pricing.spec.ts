// M5: costing. Bar segments, the quote hand-over to the new-order dialog, the unit-economics panel and the price
// calculator (asks the gateway, shows the price and the WhatsApp text, turns a catalog quote into an order).
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import UnitEconomicsPanel from '@/components/finance/UnitEconomicsPanel.vue'
import { keepQuoteForOrder, type Quote, segments, takeQuoteForOrder, type UnitEconomicsReport } from '@/lib/pricing'
import { useCatalog } from '@/stores/catalog'
import PricingView from '@/views/PricingView.vue'

import { json, on, serve, signedInAs } from './helpers'

const economics = {
  costs: { filament: 425, electricity: 19, failures: 49, labour: 667, parts: 300, channel_fee: 0, shipping: 0 },
  unit_cost: 1460, gross_price: 2000, net_price: 1695, margin: 235, margin_pct: 13.9, profit_per_print_hour: 118,
  recommended_price: 3900, target_margin_pct: 50, below_target: true,
}
const REPORT: UnitEconomicsReport = {
  products: [{ ...economics, sku: 'HOOK', name: 'Hook', niche: 'home', niche_name: 'Home décor', niche_color: 1, estimated: false,
    grams: 50, print_minutes: 120, post_minutes: 10 }],
  failure_rate: 0.1, failure_days: 30, channel: null, channel_fee_pct: 0, labour_agorot_per_hour: 4000, below_target: 1,
}
const QUOTE: Quote = {
  ...economics, gross_price: 3900, net_price: 3305, margin: 1845, margin_pct: 55.8, below_target: false, unit_price: 3900,
  quantity: 3, extras_agorot: 0, shipping_agorot: 2500, total_agorot: 14200, total_margin_agorot: 5535, print_hours: 6,
  grams: 50, print_minutes: 120, sku: 'HOOK', niche: 'home', failure_rate: 0.1, title: 'Hook', text: 'הצעת מחיר\n3 × ₪39',
}
const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })

beforeEach(() => {
  sessionStorage.clear()
  setActivePinia(createPinia())
  signedInAs('operator')
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('pricing helpers', () => {
  it('splits the net price into cost parts and the margin', () => {
    const s = segments({ ...economics, net_price: 3305, margin: 1845 })
    expect(s.map((x) => x.part)).toEqual(['filament', 'electricity', 'failures', 'labour', 'parts', 'margin'])
    expect(Math.round(s.reduce((a, x) => a + x.pct, 0))).toBe(100)
    // a loss: the bar is the cost, no margin segment
    expect(segments({ ...economics, net_price: 1000, margin: -460 }).some((x) => x.part === 'margin')).toBe(false)
  })

  it('hands a quote to the new-order dialog once', () => {
    keepQuoteForOrder({ sku: 'HOOK', quantity: 3, unit_price_agorot: 3900, shipping_agorot: 2500 })
    expect(takeQuoteForOrder()).toMatchObject({ sku: 'HOOK', quantity: 3 })
    expect(takeQuoteForOrder()).toBeNull()
  })
})

describe('unit economics panel', () => {
  it('shows each product below its niche target with the price for the target, per channel', async () => {
    const calls = serve(on('GET', /\/api\/v1\/finance\/unit-economics/, () => json(200, REPORT)))
    const w = mount(UnitEconomicsPanel)
    await flushPromises()
    expect(w.get('[data-testid="ue-below"]').text()).toContain('1 product below')
    expect(w.get('[data-testid="ue-HOOK"]').text()).toContain('13.9%')
    expect(w.get('[data-testid="ue-rec-HOOK"]').text()).toContain('39')
    await w.get('[data-testid="ue-channel"]').setValue('etsy')
    await flushPromises()
    expect(calls.at(-1)?.url).toBe('/api/v1/finance/unit-economics?channel=etsy')
  })
})

describe('price calculator', () => {
  it('prices a catalog product, shows the quote text and turns it into an order', async () => {
    vi.useFakeTimers()
    const calls = serve(on('POST', '/api/v1/pricing/quote', () => json(200, QUOTE)),
      on('GET', /\/api\/v1\/(catalog|niches)/, () => json(200, { products: [], categories: [], counts: {}, niches: [] })))
    useCatalog().products = [{ sku: 'HOOK', name: 'Hook', active: true, est_grams: 50, est_minutes: 120, price_agorot: 2000 } as never]
    useCatalog().loaded = true
    const router = anyRoute()
    const push = vi.spyOn(router, 'push')
    const w = mount(PricingView, { global: { plugins: [router] } })
    await w.get('[data-testid="q-sku"]').setValue('HOOK')
    await w.get('[data-testid="q-qty"]').setValue('3')
    await w.get('[data-testid="q-shipping"]').setValue('25')
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    const asked = calls.find((c) => c.url === '/api/v1/pricing/quote')?.body as Record<string, unknown>
    expect(asked).toMatchObject({ sku: 'HOOK', quantity: 3, shipping_agorot: 2500, channel: 'whatsapp', grams: null })
    expect(w.get('[data-testid="q-unit-price"]').text()).toContain('39')
    expect(w.get('[data-testid="q-total"]').text()).toContain('142')
    expect(w.get('[data-testid="q-text"]').text()).toContain('3 × ₪39')
    await w.get('[data-testid="q-customer"]').setValue('Noa')
    await w.get('[data-testid="q-order"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/orders', query: { new: 'quote' } })
    expect(takeQuoteForOrder()).toMatchObject({ sku: 'HOOK', quantity: 3, unit_price_agorot: 3900, shipping_agorot: 2500, customer_name: 'Noa' })
  })

  it('asks for numbers before pricing a custom print', async () => {
    serve(on('GET', /\/api\/v1\//, () => json(200, { products: [], niches: [] })))
    const w = mount(PricingView, { global: { plugins: [anyRoute()] } })
    await w.get('[data-testid="q-grams"]').setValue('100')
    expect(w.text()).toContain('type the grams and print minutes')
    expect(w.find('[data-testid="q-unit-price"]').exists()).toBe(false)
  })
})
