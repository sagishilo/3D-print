import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SpoolCard from '@/components/filament/SpoolCard.vue'
import SpoolDialog from '@/components/filament/SpoolDialog.vue'
import { describe as describeEvent } from '@/lib/events'
import { dailyTotals, inPrintGrams, type Reservation, type Spool, where } from '@/lib/filament'
import type { Printer } from '@/lib/types'

import { json, on, serve, signedInAs } from './helpers'

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs()
})
afterEach(() => vi.unstubAllGlobals())

const spool = (id: string, over: Partial<Spool> = {}): Spool => ({
  spool_id: id, material: 'PLA', color: 'black', brand: 'Bambu', initial_grams: 1000, remaining_grams: 500,
  printer_id: 'p1', slot: 'AMS 1 · slot 1', status: 'loaded', location: null, tag: null, weighed_at: null, created_at: '', ...over,
})
const printer = (id: string, status: string, plate: string | null, progress: number): Printer => ({
  id, vendor: 'bambu', protocol: 'Bambu MQTT', model: 'p1', simulated: true, bed_mm: [256, 256], nozzle_mm: 0.4,
  material: 'PLA', color: 'black', online: true, busy: true, bed_clear: false, updated_at: '', camera: false,
  state: { printer_id: id, vendor: 'bambu', status: status as never, plate_id: plate, progress, observed_at: '' },
})
const reservation = (plate: string, grams: number, printerId = 'p1'): Reservation => ({
  plate_id: plate, printer_id: printerId, material: 'PLA', color: 'black', grams, created_at: '',
})

describe('live grams while printing', () => {
  it('take the printed share of the plate from the loaded spools, lowest first (AMS refill)', () => {
    const spools = [spool('big', { remaining_grams: 800 }), spool('small', { remaining_grams: 30 }), spool('shelf', { status: 'stock', printer_id: null })]
    const used = inPrintGrams(spools, [reservation('plate-1', 200)], [printer('p1', 'printing', 'plate-1', 0.5)])
    expect(Object.fromEntries(used)).toEqual({ small: 30, big: 70 }) // 100 g printed so far
  })

  it('count nothing for a plate that is only scheduled, or on another printer', () => {
    const spools = [spool('a')]
    expect(inPrintGrams(spools, [reservation('plate-2', 200)], [printer('p1', 'printing', 'plate-1', 0.5)]).size).toBe(0)
    expect(inPrintGrams(spools, [reservation('plate-1', 200, 'p2')], [printer('p1', 'printing', 'plate-1', 0.5)]).size).toBe(0)
  })

  it('describe where a spool is and total daily use', () => {
    expect(where(spool('a'))).toBe('p1 · AMS 1 · slot 1')
    expect(where(spool('b', { printer_id: null, status: 'stock', location: 'B2' }))).toBe('Shelf B2')
    const days = dailyTotals(
      [{ day: '2026-11-13', material: 'PLA', color: 'black', grams: 400.4 }, { day: '2026-11-13', material: 'PLA', color: 'white', grams: 100 }],
      3, new Date('2026-11-14T12:00:00Z'),
    )
    expect(days).toEqual([{ day: '2026-11-12', grams: 0 }, { day: '2026-11-13', grams: 500 }, { day: '2026-11-14', grams: 0 }])
  })
})

describe('SpoolCard', () => {
  it('shows live grams, percent, place and days left', () => {
    const w = mount(SpoolCard, { props: { spool: spool('SP-1', { remaining_grams: 312 }), inPrint: 12, dailyGrams: 150 } })
    expect(w.text()).toContain('300 g')
    expect(w.text()).toContain('30%')
    expect(w.text()).toContain('2.0 d')
    expect(w.text()).toContain('printing · −12 g so far')
    expect(w.text()).toContain('Active')
  })
})

describe('SpoolDialog', () => {
  it('registers a scanned spool under its label', async () => {
    const calls = serve(on('POST', '/api/v1/commands/spools.register', () => json(202, { accepted: true })))
    const w = mount(SpoolDialog, { props: { spool: null, tag: 'QR-0042' } })
    await w.get('[data-testid=reg-color]').setValue('clear')
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(calls[0].body).toEqual({ material: 'PLA', color: 'clear', brand: '', initial_grams: 1000, location: '', tag: 'QR-0042' })
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('weighs a spool', async () => {
    const calls = serve(on('POST', '/api/v1/commands/spools.adjust', () => json(202, { accepted: true })))
    const w = mount(SpoolDialog, { props: { spool: spool('SP-9') } })
    await w.get('[data-testid=weigh-grams]').setValue('412')
    await w.findAll('button').find((b) => b.text() === 'Save weight')!.trigger('click')
    await flushPromises()
    expect(calls[0].body).toEqual({ spool_id: 'SP-9', remaining_grams: 412, reason: 'weighed' })
  })
})

describe('inventory events', () => {
  const env = (type: string, data: Record<string, unknown>) => ({ id: 'x', source: '/agents/inventory', type, time: '', kind: 'event' as const, correlationid: 'x', data })
  it('read as the alert says', () => {
    const d = describeEvent(env('io.printfarm.inventory.alert.raised.v1', { alert_kind: 'spool_short', severity: 'warning', message: 'p1: plate needs ~110 g', printer_id: 'p1' }))
    expect(d).toMatchObject({ text: 'p1: plate needs ~110 g', tone: 'warn', category: 'stock', printerId: 'p1' })
    expect(describeEvent(env('io.printfarm.inventory.spool.adjusted.v1', { spool_id: 'SP-1', remaining_grams: 412, delta_grams: -38, by: 'admin' })).text)
      .toBe('Spool SP-1 weighed: 412 g (-38 g, admin)')
  })
})
