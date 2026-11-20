// W2-B: shipping an order (Israel Post in test or live mode, a carrier typed by hand, the address prefilled), a
// parcel's label, tracking and moves, Finance > Documents (issued, failed, payments without one) and the live feed.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import DocumentsPanel from '@/components/finance/DocumentsPanel.vue'
import ShipPanel from '@/components/orders/ShipPanel.vue'
import { describe as describeEvent } from '@/lib/events'
import type { OrderDetail } from '@/lib/order-types'
import { useConfirm } from '@/stores/confirm'

import { json, on, serve, signedInAs } from './helpers'

const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })

const ORDER = {
  order_id: 'shp-1', customer_name: 'Noa Levi', state: 'QC', qc: 'passed', shipments: [],
  ship_to: { name: 'Noa Levi', street: 'Herzl 1', city: 'Tel Aviv', zip: '6100001', phone: '050-1' },
} as unknown as OrderDetail
const IP = { connector_id: 'israel-post', label: 'Israel Post', enabled: true, mode: 'fake', tracking_pattern: '[A-Z]{2}\\d{9}[A-Z]{2}|\\d{9,15}' }
const PARCEL = {
  shipment_id: 'pcl-shp-1-1', connector_id: 'israel-post', carrier: 'Israel Post', service: 'registered', tracking: 'RR900000042IL',
  tracking_url: 'https://israelpost.co.il/itemtrace?itemcode=RR900000042IL', label_url: '/api/v1/shipments/pcl-shp-1-1/label',
  status: 'label_created', ship_to: {}, events: [{ status: 'label_created', at: '2026-11-20T10:00:00Z', text: '', by: 'dana' }],
  created_by: 'dana', created_at: '2026-11-20T10:00:00Z', status_at: '2026-11-20T10:00:00Z',
}

const posts = (calls: { method: string; url: string; body: unknown }[]) =>
  calls.filter((c) => c.method === 'POST').map((c) => [c.url.split('/').at(-1), c.body])

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs('operator')
})
afterEach(() => vi.unstubAllGlobals())

describe('shipping an order', () => {
  it('ships with Israel Post (test mode) to the store’s address', async () => {
    const calls = serve(on('GET', /\/api\/v1\/shipments/, () => json(200, { shipments: [], couriers: [IP, { ...IP, connector_id: 'off', enabled: false }] })),
      on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    const w = mount(ShipPanel, { props: { order: ORDER, qcPassed: true } })
    await flushPromises()
    expect(w.get('[data-testid="ship-courier"]').findAll('option').map((o) => o.text())).toEqual(['Israel Post (test mode)', 'Another carrier (type it)'])
    expect(w.find('[data-testid="ship-tracking"]').exists()).toBe(false)        // the test courier makes the number
    expect((w.get('[data-testid="ship-street"]').element as HTMLInputElement).value).toBe('Herzl 1')
    await w.get('[data-testid="ship-form"]').trigger('submit')
    await flushPromises()
    expect(posts(calls)).toEqual([['shipments.create', { order_id: 'shp-1', carrier: 'israel-post',
      ship_to: { name: 'Noa Levi', street: 'Herzl 1', city: 'Tel Aviv', zip: '6100001', phone: '050-1' } }]])
    expect(w.emitted('shipped')).toHaveLength(1)
  })

  it('live Israel Post needs the tracking number from Israel Post; another carrier is typed', async () => {
    const calls = serve(on('GET', /\/api\/v1\/shipments/, () => json(200, { shipments: [], couriers: [{ ...IP, mode: 'live' }] })),
      on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    const w = mount(ShipPanel, { props: { order: { ...ORDER, ship_to: {} } as OrderDetail, qcPassed: true } })
    await flushPromises()
    expect(w.text()).toContain('Make the label at Israel Post')
    await w.get('[data-testid="ship-tracking"]').setValue('hello')
    await w.get('[data-testid="ship-form"]').trigger('submit')
    await w.get('[data-testid="ship-tracking"]').setValue('rr123456789il')
    await w.get('[data-testid="ship-form"]').trigger('submit')
    await flushPromises()
    await w.get('[data-testid="ship-courier"]').setValue('__other')
    await w.get('input[placeholder="Israel Post at the counter, pickup, a courier…"]').setValue('Friend')
    await w.get('[data-testid="ship-tracking"]').setValue('T-1')
    await w.get('input[placeholder="https://"]').setValue('javascript:alert(1)')
    await w.get('[data-testid="ship-form"]').trigger('submit')
    await w.get('input[placeholder="https://"]').setValue('https://friend.example/T-1')
    await w.get('[data-testid="ship-form"]').trigger('submit')
    await flushPromises()
    expect(posts(calls)).toEqual([
      ['shipments.create', { order_id: 'shp-1', carrier: 'israel-post', ship_to: { name: 'Noa Levi', street: '', city: '', zip: '', phone: '' }, tracking: 'rr123456789il' }],
      ['shipments.create', { order_id: 'shp-1', carrier: 'Friend', ship_to: { name: 'Noa Levi', street: '', city: '', zip: '', phone: '' }, tracking: 'T-1',
        tracking_url: 'https://friend.example/T-1' }],
    ])
  })

  it('has no Ship button before QC passes (Pass & ship does it), and shows the parcel once it exists', async () => {
    const calls = serve(on('GET', /\/api\/v1\/shipments/, () => json(200, { shipments: [], couriers: [IP] })),
      on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const before = mount(ShipPanel, { props: { order: { ...ORDER, qc: 'pending' } as OrderDetail, qcPassed: false } })
    await flushPromises()
    expect(before.find('[data-testid="ship"]').exists()).toBe(false)
    const w = mount(ShipPanel, { props: { order: { ...ORDER, state: 'SHIPPED', shipments: [PARCEL] } as unknown as OrderDetail, qcPassed: false } })
    await flushPromises()
    expect(w.get('[data-testid="parcel-status"]').text()).toBe('Label made')
    expect(w.get('[data-testid="parcel-tracking"]').attributes('href')).toBe(PARCEL.tracking_url)
    expect(w.get('[data-testid="parcel-label"]').attributes('href')).toBe('/api/v1/shipments/pcl-shp-1-1/label')
    await w.get('[data-testid="parcel-in-transit"]').trigger('click')
    await w.get('[data-testid="parcel-cancel"]').trigger('click')
    await flushPromises()
    expect(posts(calls)).toEqual([['shipments.update', { shipment_id: 'pcl-shp-1-1', status: 'in_transit' }],
      ['shipments.update', { shipment_id: 'pcl-shp-1-1', status: 'cancelled' }]])
  })
})

const DOCS = {
  documents: [
    { id: 2, connector_id: 'morning', payment_id: 'p2', order_id: 'shp-1', kind: 'credit', amount_agorot: 2500, status: 'failed', number: null,
      url: null, allocation: null, attempts: 8, error: 'Morning answered HTTP 500', created_at: '2026-11-20T10:00:00Z', issued_at: null, mode: 'live' },
    { id: 1, connector_id: 'morning', payment_id: 'p1', order_id: 'shp-1', kind: 'invoice_receipt', amount_agorot: 12300, status: 'issued',
      number: '50001', url: '/api/v1/documents/1/preview', allocation: '123456789', attempts: 1, error: null, created_at: '2026-11-20T09:00:00Z',
      issued_at: '2026-11-20T09:00:05Z', mode: 'fake' },
  ],
  counts: {}, without_document: [{ payment_id: 'p0', order_id: null, kind: 'payment', amount_agorot: 700, method: 'cash', at: '2026-11-19T10:00:00Z' }],
  providers: [{ connector_id: 'morning', label: 'Morning (Green Invoice)', enabled: true, mode: 'fake' }],
}

describe('Finance > Documents', () => {
  it('lists documents, issues failed and missing ones again', async () => {
    const calls = serve(on('GET', '/api/v1/documents', () => json(200, DOCS)), on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    const w = mount(DocumentsPanel, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    const ok = w.get('[data-testid="document-p1"]').text()
    expect(ok).toContain('Tax invoice-receipt 50001 (test document)')
    expect(ok).toContain('allocation 123456789')
    expect(w.get('[data-testid="document-p2"]').text()).toContain('Morning answered HTTP 500 · 8 tries')
    expect(w.find('[data-testid="documents-off"]').exists()).toBe(false)
    await w.get('[data-testid="document-p2"]').get('button').trigger('click')
    await w.get('[data-testid="issue-p0"]').trigger('click')
    await flushPromises()
    expect(posts(calls)).toEqual([['documents.issue', { payment_id: 'p2' }], ['documents.issue', { payment_id: 'p0' }]])
  })

  it('says when no provider is on', async () => {
    signedInAs('owner')
    serve(on('GET', '/api/v1/documents', () => json(200, { ...DOCS, documents: [], providers: [{ ...DOCS.providers[0], enabled: false }] })))
    const w = mount(DocumentsPanel, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="documents-off"]').text()).toContain('Connect Morning on Integrations')
    expect(w.find('[data-testid="issue-p0"]').exists()).toBe(false)
  })
})

describe('the live feed', () => {
  it('describes documents and parcels', () => {
    const d = (type: string, data: object) => describeEvent({ type: `io.printfarm.${type}.v1`, data } as never)
    expect(d('integrations.document.issued', { status: 'issued', number: '50001', payment_id: 'p1' }).text).toBe('Document 50001 issued for payment p1')
    expect(d('integrations.document.issued', { status: 'manual', payment_id: 'p2' }).tone).toBe('warn')
    expect(d('integrations.shipment.ready', { order_id: 'o1', carrier: 'Israel Post', tracking: 'RR1IL' }).text).toBe('Order o1: parcel with Israel Post · RR1IL')
    expect(d('integrations.shipment.refused', { order_id: 'o1', reason: 'no' }).text).toBe('Order o1 not shipped: no')
    expect(d('integrations.shipment.status', { order_id: 'o1', status: 'in_transit', text: 'Accepted' }).text).toBe('Order o1: parcel in transit (Accepted)')
  })
})
