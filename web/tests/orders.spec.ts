import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import NewOrderDialog from '@/components/orders/NewOrderDialog.vue'
import OrderBoard from '@/components/orders/OrderBoard.vue'
import { describe as describeEvent } from '@/lib/events'
import type { OrderCard, OrderState } from '@/lib/order-types'
import { dropAction, dueMatches, itemsSummary, type Line, priceOrder } from '@/lib/orders'

import { json, on, serve } from './helpers'

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

function card(state: OrderState, over: Partial<OrderCard> = {}): OrderCard {
  return {
    order_id: 'ord-1', customer_name: 'Noa', customer_ref: '', channel: 'whatsapp', due_by: null,
    created_at: '2026-11-14T08:00:00Z', state, stage: state === 'NEEDS_ATTENTION' ? 'PERSONALIZING' : (state as never),
    stage_since: '2026-11-14T08:00:00Z', stuck: false, status: '', total_agorot: 9000, paid_agorot: 0, units: 2,
    units_printed: 0, qc: null, plates: [],
    items: [
      { item_id: '1', product: 'nameplate', label: 'Nameplate', text: 'Noa', material: 'PLA', color: 'black', quantity: 1, model: { status: 'ready' } },
      { item_id: '2', product: 'nameplate', label: 'Nameplate', text: 'Dan', material: 'PLA', color: 'white', quantity: 1, model: { status: 'generating' } },
    ],
    ...over,
  }
}

describe('board drops (mirror the saga)', () => {
  it('dispatch only the next step and say why not otherwise', () => {
    expect(dropAction(card('RECEIVED'), 'PERSONALIZING')).toEqual({ ok: true, kind: 'advance', to: 'PERSONALIZING' })
    expect(dropAction(card('LEAD'), 'RECEIVED')).toEqual({ ok: true, kind: 'advance', to: 'RECEIVED' })
    expect(dropAction(card('PERSONALIZING'), 'SCHEDULING')).toEqual({ ok: false, reason: 'nameplate of item 2 is not ready yet' })
    expect(dropAction(card('RECEIVED'), 'PRINTING')).toEqual({ ok: false, reason: 'printing starts when a printer takes the plate' })
    expect(dropAction(card('QC', { qc: 'pending' }), 'SHIPPED')).toEqual({ ok: false, reason: 'pass QC first' })
    expect(dropAction(card('QC', { qc: 'passed' }), 'SHIPPED')).toEqual({ ok: true, kind: 'ship' })
    expect(dropAction(card('NEEDS_ATTENTION'), 'PERSONALIZING')).toEqual({ ok: true, kind: 'retry_texts' })
    expect(dropAction(card('NEEDS_ATTENTION', { stage: 'PRINTING' }), 'SCHEDULING')).toMatchObject({ ok: true, to: 'SCHEDULING' })
  })
})

describe('pricing preview (mirrors contracts/orders.py)', () => {
  const line = (over: Partial<Line> = {}): Line => ({ product: 'nameplate', text: 'A', material: 'PLA', color: 'black', quantity: 1, unit_price_agorot: null, ...over })

  it('totals lines, discount and shipping, and flags bad input', () => {
    const p = priceOrder([line({ quantity: 3, unit_price_agorot: 4000 }), line()], 1000, 2500)
    expect(p).toMatchObject({ lines: [12000, 4500], subtotal: 16500, total: 18000, units: 4, error: null })
    expect(priceOrder([line()], 99999).error).toBe('the discount is larger than the items’ total')
    expect(priceOrder([line({ text: ' ' })]).error).toBe('every nameplate needs its text')
    expect(priceOrder([]).error).toBe('an order needs at least one item')
  })

  it('summarises items and due dates', () => {
    expect(itemsSummary(card('RECEIVED'))).toBe('Nameplate ×1 + 1 more')
    const now = new Date('2026-11-14T10:00:00')
    expect(dueMatches('2026-11-14T18:00:00', 'today', now)).toBe(true)
    expect(dueMatches('2026-11-14T08:00:00', 'overdue', now)).toBe(true)
    expect(dueMatches('2026-11-19T08:00:00', 'week', now)).toBe(true)
    expect(dueMatches(null, 'today', now)).toBe(false)
  })
})

describe('order events', () => {
  const env = (type: string, data: Record<string, unknown>) => ({ id: 'x', source: '/agents/coordinator', type, time: '', kind: 'event' as const, correlationid: 'x', data })
  it('read like the board', () => {
    expect(describeEvent(env('io.printfarm.order.stage.changed.v1', { order_id: 'ord-1', from_state: 'RECEIVED', to_state: 'PERSONALIZING', by: 'admin' })).text)
      .toBe('Order ord-1: Received → Personalization (admin)')
    expect(describeEvent(env('io.printfarm.order.move.rejected.v1', { order_id: 'ord-1', to_state: 'PRINTING', reason: 'printers drive it' })).tone).toBe('warn')
    expect(describeEvent(env('io.printfarm.order.order.created.v1', { order_id: 'ord-1', customer_name: 'Noa', units: 3, total_agorot: 13500, channel: 'etsy' })).text)
      .toBe('Order ord-1 from Noa · 3 unit(s), ₪135.00 (etsy)')
  })
})

describe('OrderBoard', () => {
  it('puts each order in its stage column, a failed one where it failed', () => {
    const w = mount(OrderBoard, {
      props: { cards: [card('RECEIVED', { order_id: 'ord-a' }), card('NEEDS_ATTENTION', { order_id: 'ord-b' }), card('CANCELLED', { order_id: 'ord-c', stage: 'CANCELLED' as never })] },
    })
    expect(w.get('[data-testid=column-RECEIVED]').text()).toContain('#a')
    expect(w.get('[data-testid=column-PERSONALIZING]').find('[data-testid=order-ord-b]').classes()).toContain('attention')
    expect(w.find('[data-testid=order-ord-c]').exists()).toBe(false)
  })

  it('reports a drop with what it would do', async () => {
    const w = mount(OrderBoard, { props: { cards: [card('RECEIVED')] } })
    const dt = { setData: () => undefined, effectAllowed: '' }
    await w.get('[data-testid=order-ord-1]').trigger('dragstart', { dataTransfer: dt })
    expect(w.get('[data-testid=column-PRINTING]').text()).toContain('printing starts when a printer takes the plate')
    await w.get('[data-testid=column-PERSONALIZING]').trigger('drop')
    expect(w.emitted('drop')![0][1]).toEqual({ ok: true, kind: 'advance', to: 'PERSONALIZING' })
  })
})

describe('NewOrderDialog', () => {
  it('previews the total and sends priced lines', async () => {
    const calls = serve(on('POST', '/api/v1/commands/orders.create', () => json(202, { accepted: true, command_id: 'c1' })))
    const w = mount(NewOrderDialog)
    await w.get('[data-testid=customer-name]').setValue('Cohen family')
    await w.get('[data-testid=line-1] input').setValue('משפחת כהן')
    await w.get('[data-testid=line-1] input[type=number]').setValue('2')
    await w.findAll('button').find((b) => b.text().includes('Add item'))!.trigger('click')
    await w.get('[data-testid=line-2] input').setValue('Dan')
    const price = w.get('[data-testid=line-2]').findAll('input').find((i) => i.attributes('inputmode') === 'decimal')!
    await price.setValue('60')
    expect(w.get('[data-testid=order-total]').text()).toContain('₪150.00')   // 2 × 45 + 60
    await w.get('form').trigger('submit')
    await flushPromises()
    const body = calls[0].body as Record<string, any>
    expect(body.customer_name).toBe('Cohen family')
    expect(body.items).toEqual([
      { product: 'nameplate', text: 'משפחת כהן', material: 'PLA', color: 'black', quantity: 2, unit_price_agorot: 4500 },
      { product: 'nameplate', text: 'Dan', material: 'PLA', color: 'black', quantity: 1, unit_price_agorot: 6000 },
    ])
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('will not submit without a customer', async () => {
    const w = mount(NewOrderDialog)
    await w.get('[data-testid=line-1] input').setValue('A')
    expect(w.get('[role=alert]').text()).toBe('the customer’s name is missing')
    expect(w.get('button[type=submit]').attributes('disabled')).toBeDefined()
  })
})
