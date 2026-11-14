import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CalibrationPanel from '@/components/fleet/CalibrationPanel.vue'
import HardwarePanel from '@/components/fleet/HardwarePanel.vue'
import PrinterTile from '@/components/live/PrinterTile.vue'
import ConfirmHost from '@/components/ui/ConfirmHost.vue'
import { describe as describeEvent } from '@/lib/events'
import { describePlan, planBatch, severityOf, sourceLabel, wifiQuality } from '@/lib/fleet'
import type { Printer, PrinterDetail, PrinterStatus } from '@/lib/types'
import { useConfirm } from '@/stores/confirm'
import { displayStatus, usePrinters } from '@/stores/printers'

import { json, on, serve } from './helpers'

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

function printer(id: string, status: PrinterStatus | 'offline', over: Partial<Printer> = {}): Printer {
  return {
    id, vendor: 'moonraker', protocol: 'Klipper', model: 'klipper', simulated: true, bed_mm: [300, 300], nozzle_mm: 0.4,
    material: 'PLA', color: 'black', online: status !== 'offline', busy: false, bed_clear: true, updated_at: '', camera: false,
    state: status === 'offline' ? null : {
      printer_id: id, vendor: 'moonraker', status, progress: status === 'printing' ? 0.4 : 0, nozzle_temp: 210, nozzle_target: 210,
      observed_at: '2026-11-14T09:00:00Z',
    },
    ...over,
  }
}

describe('batch actions', () => {
  const fleet = [
    printer('a', 'printing'),
    printer('b', 'paused'),
    printer('c', 'idle'),
    printer('d', 'finished', { bed_clear: false }),
    printer('e', 'offline'),
  ]

  it('apply only to printers in a fitting state and say why the others are skipped', () => {
    const pause = planBatch('pause', fleet, displayStatus)
    expect(pause.eligible.map((p) => p.id)).toEqual(['a'])
    expect(pause.skipped.map((s) => `${s.printer.id}: ${s.reason}`)).toEqual([
      'b: not printing (paused)', 'c: not printing (idle)', 'd: not printing (finished)', 'e: offline',
    ])
    expect(planBatch('resume', fleet, displayStatus).eligible.map((p) => p.id)).toEqual(['b'])
    expect(planBatch('cancel', fleet, displayStatus).eligible.map((p) => p.id)).toEqual(['a', 'b'])
    expect(planBatch('clear_bed', fleet, displayStatus).eligible.map((p) => p.id)).toEqual(['d'])
    expect(planBatch('home', fleet, displayStatus).eligible.map((p) => p.id)).toEqual(['c', 'd'])
    expect(describePlan(planBatch('resume', fleet.slice(0, 2), displayStatus))).toBe('1 printer: b · skipped: a (not paused (printing))')
  })

  it('never home a printer that is calibrating', () => {
    const cal = printer('x', 'idle', { calibration: { calibration_id: 'c', routine: 'bed_level', requested_by: 'admin', started_at: '' } })
    expect(planBatch('home', [cal], displayStatus).skipped[0].reason).toBe('calibrating')
  })
})

describe('fleet helpers', () => {
  it('rate severities like the gateway', () => {
    expect(severityOf('printer.failure.detected', { severity: 'critical' })).toBe('critical')
    expect(severityOf('printer.pause.unconfirmed', {})).toBe('critical')
    expect(severityOf('fleet.plate.finished', { succeeded: false })).toBe('warning')
    expect(severityOf('printer.calibration.finished', { succeeded: false, started: false })).toBe('info')
    expect(severityOf('printer.calibration.finished', { succeeded: false })).toBe('warning')
    expect(severityOf('printer.print.started', {})).toBe('info')
  })

  it('read Wi-Fi strength and message sources', () => {
    expect(wifiQuality(-52).label).toBe('good')
    expect(wifiQuality(-68).tone).toBe('warn')
    expect(wifiQuality(-80).label).toBe('weak')
    expect(sourceLabel('/agents/telemetry')).toBe('Telemetry')
    expect(sourceLabel('/gateway')).toBe('Dashboard')
  })

  it('describe calibration events', () => {
    const env = (data: Record<string, unknown>) => ({
      id: 'x', source: '/agents/telemetry', type: 'io.printfarm.printer.calibration.finished.v1', time: '', kind: 'event' as const,
      correlationid: 'x', data,
    })
    expect(describeEvent(env({ printer_id: 'v', routine: 'bed_level', succeeded: true, seconds: 95 })).text).toBe('v: bed level done in 1 m')
    const refused = describeEvent(env({ printer_id: 'v', routine: 'home', succeeded: false, started: false, detail: 'refused: printer is printing' }))
    expect(refused).toMatchObject({ text: 'v: home not started · refused: printer is printing', tone: 'idle' })
  })
})

describe('confirm dialog', () => {
  it('keeps Confirm disabled until the required statement is ticked', async () => {
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.open = true }
    HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.open = false }
    const w = mount(ConfirmHost, { attachTo: document.body })
    const answer = useConfirm().ask({ title: 'Home?', text: 'Moves', confirmLabel: 'Start', requireCheck: 'The bed is empty' })
    await flushPromises()
    const start = w.findAll('button').find((b) => b.text() === 'Start')!
    expect(start.attributes('disabled')).toBeDefined()
    await w.get('[data-testid=confirm-check]').setValue(true)
    expect(start.attributes('disabled')).toBeUndefined()
    await start.trigger('click')
    await expect(answer).resolves.toBe(true)
    w.unmount()
  })
})

describe('PrinterTile on the fleet screen', () => {
  const stubs = { CameraView: { template: '<div />' } }

  it('offers Cancel for a running print and Bed cleared for a finished one', () => {
    const run = mount(PrinterTile, { props: { printer: printer('a', 'printing'), details: true, history: false }, global: { stubs } })
    expect(run.find('[aria-label="Cancel the print on a"]').exists()).toBe(true)
    expect(run.find('[aria-label="Bed of a cleared"]').exists()).toBe(false)
    const done = mount(PrinterTile, { props: { printer: printer('d', 'finished', { bed_clear: false }), details: true }, global: { stubs } })
    expect(done.find('[aria-label="Bed of d cleared"]').exists()).toBe(true)
    expect(done.text()).toContain('Plate done: clear the bed')
  })

  it('shows what a calibrating printer is doing', () => {
    const p = printer('v', 'calibrating')
    p.state!.hardware = { stage: 'bed leveling' } as never
    expect(mount(PrinterTile, { props: { printer: p }, global: { stubs } }).text()).toContain('Calibrating · bed leveling')
  })
})

const detail = (p: Printer): PrinterDetail => ({
  printer: p,
  routines: [
    { key: 'home', label: 'Home all axes', help: 'Moves every axis.', max_minutes: 5, heater: null, default_temp: null },
    { key: 'nozzle_pid', label: 'Nozzle PID tuning', help: 'Heats the nozzle.', max_minutes: 15, heater: 'nozzle', default_temp: 220 },
  ],
  calibrations: [],
  sessions: [],
  pause_checks: [],
  failures: [],
  stats: { prints_7d: 0, succeeded_7d: 0, failed_7d: 0, print_hours_7d: 0 },
})

describe('CalibrationPanel', () => {
  it('is blocked while the printer prints', () => {
    usePrinters().list = [printer('v', 'printing')]
    const w = mount(CalibrationPanel, { props: { detail: detail(printer('v', 'printing')) } })
    expect(w.text()).toContain('The printer is printing: calibrate when it is idle.')
    expect(w.findAll('button').every((b) => b.attributes('disabled') !== undefined)).toBe(true)
  })

  it('sends the routine with the confirmed bed and the PID target', async () => {
    const calls = serve(on('POST', '/api/v1/commands/printers.calibrate', () => json(202, { accepted: true })))
    usePrinters().list = [printer('v', 'idle')]
    const w = mount(CalibrationPanel, { props: { detail: detail(printer('v', 'idle')) } })
    await w.get('[data-testid=routine-nozzle_pid] input').setValue('240')
    await w.get('[data-testid=routine-nozzle_pid] button').trigger('click')
    const confirm = useConfirm()
    expect(confirm.current?.requireCheck).toBe('The bed is empty and nothing blocks the print head.')
    expect(confirm.current?.text).toContain('target 240 °C')
    confirm.answer(true)
    await flushPromises()
    expect(calls[0].body).toEqual({ printer_id: 'v', routine: 'nozzle_pid', bed_clear_confirmed: true, temperature: 240 })
    expect(w.emitted('requested')).toHaveLength(1)
  })
})

describe('HardwarePanel', () => {
  it('shows fans, Wi-Fi, firmware, health codes and AMS trays', () => {
    const p = printer('b', 'printing', { vendor: 'bambu', protocol: 'Bambu MQTT' })
    p.state!.hardware = {
      chamber_temp: 31.2, fans: { part: 100, hotend: 100 }, speed_mode: 'sport', speed_pct: 124, nozzle_diameter: 0.4,
      nozzle_type: 'hardened_steel', wifi_dbm: -72, firmware: '01.08.05.00', stage: null, homed_axes: null, bed_mesh: null,
      host_load: null, hms: ['0300_0100_0001_0007'],
      ams: [{ unit: 0, humidity_level: 4, temp: 24, trays: [{ unit: 0, slot: 0, material: 'PLA', color: '#000000', remaining_pct: 10 }] }],
    }
    const w = mount(HardwarePanel, { props: { printer: p } })
    expect(w.get('[data-testid=hw-fans]').text()).toContain('Part cooling')
    expect(w.get('[data-testid=hw-connection]').text()).toMatch(/-72 dBm\s*weak/)
    expect(w.get('[data-testid=hw-connection]').text()).toContain('01.08.05.00')
    expect(w.get('[data-testid=hw-health]').text()).toContain('HMS 0300_0100_0001_0007')
    expect(w.get('[data-testid=hw-ams]').text()).toContain('AMS 1 · humidity index 4 · 24 °C')
    expect(w.text()).toContain('Sport · 124%')
    expect(w.find('.low').exists()).toBe(true) // 10 % left is flagged
  })
})
