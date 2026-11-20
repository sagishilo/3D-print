// W2-A: the Store orders screen (the "map this" queue, store orders and what became of them, the product map, the
// tracking sent back), a connector's settings and "New test orders" on Integrations, and the live-feed lines.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { describe as describeEvent } from '@/lib/events'
import { useConfirm } from '@/stores/confirm'
import { keyLabel } from '@/stores/storeOrders'
import IntegrationsView from '@/views/IntegrationsView.vue'
import StoreView from '@/views/StoreView.vue'

import { json, on, serve, signedInAs } from './helpers'

const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })

const ORDERS = {
  orders: [
    { connector_id: 'shopify', external_id: 'F1-1', name: '#1001', customer: 'Noa Levi', total_agorot: 4900, currency: 'ILS',
      financial_status: 'paid', placed_at: '2026-11-20T08:00:00Z', status: 'imported', order_id: 'shp-F1-1', problems: [],
      seen_at: '2026-11-20T08:00:00Z', imported_at: '2026-11-20T08:01:00Z' },
    { connector_id: 'shopify', external_id: 'F1-2', name: '#1002', customer: 'Dan Cohen', total_agorot: 12300, currency: 'ILS',
      financial_status: 'paid', placed_at: '2026-11-20T09:00:00Z', status: 'needs_mapping', order_id: null,
      problems: [{ kind: 'unmapped', key: 'GIFT-WRAP', title: 'Gift wrap', message: 'Gift wrap: which product is this?' },
        { kind: 'problem', key: 'NP', title: 'Nameplate', message: 'Nameplate: the customer\'s "Name" is missing' }],
      seen_at: '2026-11-20T09:00:00Z', imported_at: null },
    { connector_id: 'shopify', external_id: 'F1-3', name: '#1003', customer: 'Gal', total_agorot: 4900, currency: 'ILS',
      financial_status: 'pending', placed_at: null, status: 'skipped', order_id: null, problems: [],
      seen_at: '2026-11-20T09:00:00Z', imported_at: null },
  ],
  counts: { needs_mapping: 1, waiting_payment: 0, imported: 1, cancelled: 0, skipped: 1 },
  queue: [
    { connector_id: 'shopify', external_key: 'NAMEPLATE-WHITE', title: 'Personalised nameplate', orders: ['#1002'], properties: ['Name'], variants: ['White'] },
    { connector_id: 'shopify', external_key: 'title:Gift wrap', title: 'Gift wrap', orders: ['#1002'], properties: [], variants: [] },
  ],
  stores: [{ connector_id: 'shopify', label: 'Shopify', enabled: true, mode: 'fake' }],
}
const MAPS = { maps: [{ connector_id: 'shopify', external_key: 'NAMEPLATE-BLACK', product: 'nameplate', sku: '', material: 'PLA',
  color: 'black', text_property: 'Name', updated_by: 'dana', updated_at: '2026-11-20T08:00:00Z' }], stores: ['shopify'] }
const PUSHES = { pushes: [
  { id: 1, connector_id: 'shopify', kind: 'tracking', external_id: 'F1-1', order_id: 'shp-F1-1', payload: { carrier: 'Israel Post', tracking: 'RR1' },
    status: 'sent', attempts: 1, error: null, created_at: '2026-11-20T10:00:00Z', sent_at: '2026-11-20T10:00:05Z' },
  { id: 2, connector_id: 'shopify', kind: 'tracking', external_id: 'F0-9', order_id: 'shp-F0-9', payload: {},
    status: 'failed', attempts: 8, error: 'Shopify answered HTTP 500', created_at: '2026-11-19T10:00:00Z', sent_at: null },
] }
const CATALOG = { products: [{ sku: 'VASE-1', name: 'Spiral vase' }], categories: [], counts: {} }

function serveStore(...extra: ReturnType<typeof on>[]) {
  return serve(on('GET', '/api/v1/store/orders', () => json(200, ORDERS)), on('GET', '/api/v1/store/maps', () => json(200, MAPS)),
    on('GET', '/api/v1/store/pushes', () => json(200, PUSHES)), on('GET', /\/api\/v1\/catalog\/products/, () => json(200, CATALOG)),
    ...extra)
}

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs('operator')
})
afterEach(() => vi.unstubAllGlobals())

describe('the Store orders screen', () => {
  it('shows the queue, the orders with their problems, the map and the tracking sent back', async () => {
    serveStore()
    const w = mount(StoreView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    const q = w.get('[data-testid="map-NAMEPLATE-WHITE"]').text()
    expect(q).toContain('1 order(s) wait: #1002')
    expect(q).toContain('The customer filled in: Name')
    // prefilled from what the customers typed: a nameplate in the variant's colour, text from "Name"
    expect((w.get('[data-testid="map-product-NAMEPLATE-WHITE"]').element as HTMLSelectElement).value).toBe('nameplate')
    expect((w.get('[data-testid="map-color-NAMEPLATE-WHITE"]').element as HTMLSelectElement).value).toBe('white')
    expect(w.get('[data-testid="map-title:Gift wrap"]').text()).toContain('Gift wrap')
    expect(w.get('[data-testid="status-F1-1"]').text()).toBe('Imported')
    expect(w.get('[data-testid="ours-F1-1"]').text()).toBe('shp-F1-1')
    const waiting = w.get('[data-testid="store-order-F1-2"]').text()
    expect(waiting).toContain('Waits for you')
    expect(waiting).toContain('GIFT-WRAP: which product is this?')
    expect(waiting).toContain('"Name" is missing')
    expect(w.get('[data-testid="skumap-NAMEPLATE-BLACK"]').text()).toContain('nameplate, PLA black, text from "Name"')
    expect(w.get('[data-testid="push-shp-F1-1"]').text()).toContain('RR1 (Israel Post)')
    expect(w.get('[data-testid="push-shp-F0-9"]').text()).toContain('Failed: do it in the store by hand · 8 tries')
    expect(w.find('[data-testid="store-off"]').exists()).toBe(false)
    // filter by status
    await w.get('[data-testid="filter-skipped"]').trigger('click')
    expect(w.findAll('[data-testid^="store-order-"]').map((r) => r.attributes('data-testid'))).toEqual(['store-order-F1-3'])
  })

  it('maps products, skips and takes back orders, and forgets a mapping (asking first)', async () => {
    const calls = serveStore(on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const w = mount(StoreView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    await w.get('[data-testid="map-save-NAMEPLATE-WHITE"]').trigger('submit')
    // a catalog product needs its SKU first
    await w.get('[data-testid="map-save-title:Gift wrap"]').trigger('submit')
    await w.get('[data-testid="map-sku-title:Gift wrap"]').setValue('VASE-1')
    await w.get('[data-testid="map-save-title:Gift wrap"]').trigger('submit')
    await w.get('[data-testid="map-product-title:Gift wrap"]').setValue('ignore')
    await w.get('[data-testid="map-save-title:Gift wrap"]').trigger('submit')
    await w.get('[data-testid="skip-F1-2"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="unskip-F1-3"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="unmap-NAMEPLATE-BLACK"]').trigger('click')
    await flushPromises()
    const sent = calls.filter((c) => c.method === 'POST').map((c) => [c.url.split('/').at(-1), c.body])
    expect(sent).toEqual([
      ['integrations.map_sku', { connector_id: 'shopify', external_key: 'NAMEPLATE-WHITE', product: 'nameplate', material: 'PLA', color: 'white', text_property: 'Name' }],
      ['integrations.map_sku', { connector_id: 'shopify', external_key: 'title:Gift wrap', product: 'catalog', sku: 'VASE-1' }],
      ['integrations.map_sku', { connector_id: 'shopify', external_key: 'title:Gift wrap', product: 'ignore' }],
      ['integrations.store_order', { connector_id: 'shopify', external_id: 'F1-2', action: 'skip' }],
      ['integrations.store_order', { connector_id: 'shopify', external_id: 'F1-3', action: 'unskip' }],
      ['integrations.map_sku', { connector_id: 'shopify', external_key: 'NAMEPLATE-BLACK', remove: true }],
    ])
  })

  it('says when no store is switched on; viewers cannot change anything', async () => {
    signedInAs('viewer')
    serve(on('GET', '/api/v1/store/orders', () => json(200, { ...ORDERS, stores: [{ ...ORDERS.stores[0], enabled: false }] })),
      on('GET', '/api/v1/store/maps', () => json(200, MAPS)), on('GET', '/api/v1/store/pushes', () => json(200, PUSHES)),
      on('GET', /\/api\/v1\/catalog\/products/, () => json(200, CATALOG)))
    const w = mount(StoreView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="store-off"]').text()).toContain('No store is switched on yet.')
    expect(w.get('[data-testid="skip-F1-2"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="unmap-NAMEPLATE-BLACK"]').attributes('disabled')).toBeDefined()
  })
})

const CONNECTOR = {
  connector_id: 'shopify', kind: 'store', label: 'Shopify', enabled: true, mode: 'fake', interval_s: 300,
  secrets_status: { SHOPIFY_CLIENT_ID: false, SHOPIFY_CLIENT_SECRET: false, SHOPIFY_ACCESS_TOKEN: false }, secrets_missing: [],
  health: 'ok', last_run_at: null, last_ok_at: null, last_error: null, consecutive_failures: 0, next_run_at: null, runs: 0, items: 0,
  mapped: 0, updated_by: 'agent',
  settings: { shop: '', api_version: '2026-07', only_paid: true, fake_batch: 4 },
  settings_schema: [{ key: 'shop', label: 'Shop address', type: 'str' }, { key: 'api_version', label: 'Admin API version', type: 'str' },
    { key: 'only_paid', label: 'Import only paid orders', type: 'bool' }, { key: 'fake_batch', label: 'Test orders batch', type: 'int' }],
}

describe('a connector’s settings on Integrations', () => {
  it('saves only what changed, and makes new test orders in fake mode', async () => {
    signedInAs('owner')
    const calls = serve(on('GET', '/api/v1/integrations', () => json(200, { connectors: [CONNECTOR] })),
      on('GET', '/api/v1/notifications', () => json(200, { notifications: [] })),
      on('GET', /\/runs/, () => json(200, { runs: [] })), on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    const w = mount(IntegrationsView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.find('[data-testid="setting-shopify-fake_batch"]').exists()).toBe(false)     // the button handles it
    expect(w.get('[data-testid="settings-save-shopify"]').attributes('disabled')).toBeDefined()
    await w.get('[data-testid="setting-shopify-shop"]').setValue('my-shop.myshopify.com')
    await w.get('[data-testid="setting-shopify-only_paid"]').setValue(false)
    await w.get('[data-testid="settings-shopify"]').trigger('submit')
    await flushPromises()
    await w.get('[data-testid="test-orders-shopify"]').trigger('click')
    await flushPromises()
    const sent = calls.filter((c) => c.method === 'POST').map((c) => [c.url.split('/').at(-1), c.body])
    expect(sent).toEqual([
      ['integrations.configure', { connector_id: 'shopify', settings: { shop: 'my-shop.myshopify.com', only_paid: false } }],
      ['integrations.configure', { connector_id: 'shopify', settings: { fake_batch: 5 } }],
      ['integrations.run', { connector_id: 'shopify' }],
    ])
  })
})

describe('helpers and the live feed', () => {
  it('label store keys and describe the new events', () => {
    expect(keyLabel('title:Gift wrap')).toBe('Gift wrap')
    expect(keyLabel('NP-1')).toBe('NP-1')
    const d = (type: string, data: object) => describeEvent({ type: `io.printfarm.${type}.v1`, data } as never)
    expect(d('integrations.store_order.changed', { name: '#1001', status: 'imported', order_id: 'shp-1' }).text).toBe('Store order #1001 imported as shp-1')
    expect(d('integrations.store_order.changed', { name: '#1002', status: 'needs_mapping' }).tone).toBe('warn')
    expect(d('integrations.sku.mapped', { external_key: 'X', product: 'catalog', sku: 'VASE-1', by: 'dana' }).text).toBe('Store product X → VASE-1 (dana)')
    expect(d('integrations.push.finished', { connector_id: 'shopify', order_id: 'shp-1', ok: false, attempts: 2, error: 'no' }).text)
      .toBe('shopify did not take the tracking of shp-1 (try 2): no')
  })
})
