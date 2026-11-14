import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MoneyDialog from '@/components/finance/MoneyDialog.vue'
import { changeLabel, daysSince, exportUrl, shortLabel } from '@/lib/finance'

import { json, on, serve } from './helpers'

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('finance helpers', () => {
  it('label changes, periods and exports', () => {
    expect(changeLabel(9.4, 'Oct')).toBe('▲ 9.4% vs Oct')
    expect(changeLabel(-3, 'Oct')).toBe('▼ 3% vs Oct')
    expect(changeLabel(null, 'Oct')).toBe('no data for Oct')
    expect(shortLabel({ kind: 'month', start: '', end: '', label: 'Oct 2026' })).toBe('Oct')
    expect(shortLabel({ kind: 'quarter', start: '', end: '', label: 'Q3 2026' })).toBe('Q3 2026')
    expect(exportUrl('expenses', 'quarter', '2026-11')).toBe('/api/v1/finance/export.csv?ledger=expenses&period=quarter&month=2026-11')
    expect(daysSince('2026-11-01T00:00:00Z', Date.parse('2026-11-11T12:00:00Z'))).toBe(10)
  })
})

describe('MoneyDialog', () => {
  const receivables = [{ order_id: 'ord-1', customer_name: 'Noa', total_agorot: 9000, paid_agorot: 4000, due_agorot: 5000, shipped_at: null }]

  it('records the balance of a shipped order', async () => {
    const calls = serve(on('POST', '/api/v1/commands/payments.record', () => json(202, { accepted: true })))
    const w = mount(MoneyDialog, { props: { mode: 'payment', receivables, orderId: 'ord-1' } })
    expect((w.get('[data-testid=money-amount]').element as HTMLInputElement).value).toBe('50.00')
    await w.get('form').trigger('submit')
    await flushPromises()
    const body = calls[0].body as Record<string, unknown>
    expect(body).toMatchObject({ order_id: 'ord-1', amount_agorot: 5000, method: 'bit' })
    expect(typeof body.paid_at).toBe('string')
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('adds an electricity bill as a cash expense with its VAT', async () => {
    const calls = serve(on('POST', '/api/v1/commands/expenses.create', () => json(202, { accepted: true })))
    const w = mount(MoneyDialog, { props: { mode: 'expense' } })
    await w.get('[data-testid=expense-category]').setValue('electricity')
    expect(w.text()).toContain('replaces the estimate from print hours')
    await w.get('[data-testid=money-amount]').setValue('412.30')
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(calls[0].body).toMatchObject({ category: 'electricity', gross_agorot: 41230, vat_invoice: true, estimated: false })
  })

  it('refuses an amount of zero', async () => {
    const w = mount(MoneyDialog, { props: { mode: 'expense' } })
    await w.get('form').trigger('submit')
    expect(w.get('[role=alert]').text()).toBe('enter an amount above zero')
  })
})
