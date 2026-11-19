// M4: stock beyond filament. Helpers, the shelf panel (restock, minimum and count), parts & packaging (add, reorder)
// and filament condition (Mark dried).
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ConditionPanel from '@/components/inventory/ConditionPanel.vue'
import ItemsPanel from '@/components/inventory/ItemsPanel.vue'
import ShelfPanel from '@/components/inventory/ShelfPanel.vue'
import { bomCost, type FinishedGood, itemIdFrom, type StockItem } from '@/lib/stock'
import { useConfirm } from '@/stores/confirm'
import { useStock } from '@/stores/stock'

import { json, on, serve, signedInAs } from './helpers'

const good = (over: Partial<FinishedGood> = {}): FinishedGood => ({
  sku: 'HOOK', name: 'Hook', niche: 'home', active: true, on_hand: 3, reserved: 2, free: 1, min_stock: 4, location: 'B2',
  in_production: 0, unit_cost_agorot: 300, value_agorot: 900, low: true, restock: 3, updated_at: null, ...over,
})
const item = (over: Partial<StockItem> = {}): StockItem => ({
  item_id: 'box-s', name: 'Gift box S', kind: 'packaging', unit: 'pcs', on_hand: 4, min_qty: 20, reorder_qty: 50, supplier: 'Boxes Ltd',
  lead_days: 5, cost_agorot: 120, archived: false, on_order: 0, used_by: [{ sku: 'HOOK', qty: 1 }], used_30d: 9, low: true,
  suggested_qty: 50, suggested_cost_agorot: 6000, ...over,
})
const commands = () => on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c1' }))
const sent = (calls: { url: string; body: unknown }[], name: string) => calls.find((c) => c.url === `/api/v1/commands/${name}`)?.body

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs('operator')
})
afterEach(() => vi.unstubAllGlobals())

describe('stock helpers', () => {
  it('makes item ids and prices a piece', () => {
    expect(itemIdFrom('Magnet 6×2 mm')).toBe('magnet-6-2-mm')
    expect(bomCost([{ item_id: 'm', qty: 2, cost_agorot: 120 }, { item_id: 'b', qty: 1, cost_agorot: 95 }])).toBe(335)
  })
})

describe('the shelf', () => {
  it('queues the missing pieces as a restock batch', async () => {
    const calls = serve(commands())
    useStock().shelf = [good()]
    vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const w = mount(ShelfPanel)
    expect(w.get('[data-testid="shelf-low"]').text()).toContain('HOOK')
    await w.get('[data-testid="restock-HOOK"]').trigger('click')
    await flushPromises()
    expect(sent(calls, 'catalog.batch')).toMatchObject({ items: [{ product: 'catalog', sku: 'HOOK', quantity: 3 }] })
  })

  it('saves the minimum and a count', async () => {
    const calls = serve(commands())
    useStock().shelf = [good({ low: false, restock: 0 })]
    const w = mount(ShelfPanel, { attachTo: document.body })
    await w.get('[data-testid="shelf-edit-HOOK"]').trigger('click')
    await w.get('[data-testid="shelf-min"]').setValue('6')
    await w.get('[data-testid="shelf-count"]').setValue('5')
    await w.get('[data-testid="shelf-dialog"]').trigger('submit')
    await flushPromises()
    expect(sent(calls, 'stock.rule')).toEqual({ sku: 'HOOK', min_stock: 6, location: 'B2' })
    expect(sent(calls, 'stock.count')).toEqual({ sku: 'HOOK', on_hand: 5, note: '' })
    w.unmount()
  })
})

describe('parts & packaging', () => {
  it('adds an item with an id from its name', async () => {
    const calls = serve(commands())
    const w = mount(ItemsPanel, { attachTo: document.body })
    await w.get('[data-testid="add-item"]').trigger('click')
    await w.get('[data-testid="item-name"]').setValue('Magnet 6mm')
    await w.get('[data-testid="item-on-hand"]').setValue('100')
    await w.get('[data-testid="item-min"]').setValue('10')
    await w.get('[data-testid="item-cost"]').setValue('0.40')
    await w.get('[data-testid="item-dialog"]').trigger('submit')
    await flushPromises()
    expect(sent(calls, 'items.upsert')).toMatchObject({ item_id: 'magnet-6mm', name: 'Magnet 6mm', item_kind: 'part', on_hand: 100,
      min_qty: 10, cost_agorot: 40 })
    w.unmount()
  })

  it('reorders a low item with the suggested quantity', async () => {
    const calls = serve(commands())
    useStock().items = [item()]
    vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const w = mount(ItemsPanel)
    expect(w.text()).toContain('HOOK ×1')
    await w.get('[data-testid="reorder-item-box-s"]').trigger('click')
    await flushPromises()
    expect(sent(calls, 'items.reorder')).toEqual({ item_id: 'box-s', quantity: 50 })
  })
})

describe('filament condition', () => {
  it('lists spools due for drying and humid AMS units, and marks a spool dried', async () => {
    const calls = serve(commands())
    useStock().condition = {
      spools: [{ spool_id: 'S1', material: 'PETG', color: 'black', printer_id: 'x1', slot: null, location: null, opened_at: '2026-10-01',
        dried_at: null, days_open: 30.2, dry_after_days: 21, needs_drying: true }],
      due: 1, humid: [{ printer_id: 'bambu-01', unit: 0, level: 4 }], humid_level: 4, dry_after_days: {},
    }
    const w = mount(ConditionPanel)
    expect(w.get('[data-testid="humid"]').text()).toContain('bambu-01 AMS 1: humidity index 4')
    expect(w.get('[data-testid="dry-S1"]').text()).toContain('open 30 days (dry after 21)')
    await w.get('[data-testid="mark-dried-S1"]').trigger('click')
    await flushPromises()
    expect(sent(calls, 'spools.dried')).toEqual({ spool_id: 'S1' })
    useStock().condition = { spools: [], due: 0, humid: [], humid_level: 4, dry_after_days: {} }
    await flushPromises()
    expect(w.find('[data-testid="condition-ok"]').exists()).toBe(true)
  })
})
