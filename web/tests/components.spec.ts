import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PrinterTile from '@/components/live/PrinterTile.vue'
import AgentsTable from '@/components/system/AgentsTable.vue'
import SettingsPanel from '@/components/system/SettingsPanel.vue'
import type { AgentRow, Printer, Setting } from '@/lib/types'
import { useConfirm } from '@/stores/confirm'
import { useSystem } from '@/stores/system'

import { json, on, serve, signedInAs } from './helpers'

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs()
})
afterEach(() => vi.unstubAllGlobals())

const setting = (over: Partial<Setting>): Setting => ({
  key: 'safety.auto_pause',
  label: 'Failure detection auto-pause',
  help: 'Pause the printer on a critical finding',
  default: true,
  phase: null,
  value: true,
  is_default: true,
  changed_by: null,
  changed_at: null,
  changed_value: null,
  ...over,
})

describe('SettingsPanel', () => {
  it('disables settings for later phases', () => {
    useSystem().settings = [setting({}), setting({ key: 'fleet.quiet_hours', label: 'Quiet hours', phase: 9, value: false })]
    const w = mount(SettingsPanel)
    const switches = w.findAll('[role=switch]')
    expect(switches[0].attributes('disabled')).toBeUndefined()
    expect(switches[1].attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="setting-fleet.quiet_hours"]').text()).toContain('P9')
  })

  it('sends an audited config.set command', async () => {
    const calls = serve(on('POST', '/api/v1/commands/config.set', () => json(202, { accepted: true, command_id: 'c1' })))
    useSystem().settings = [setting({ key: 'inventory.auto_reorder', label: 'Auto-reorder filament', value: true })]
    const w = mount(SettingsPanel)
    await w.get('[role=switch]').trigger('click')
    await flushPromises()
    expect(calls[0].body).toEqual({ key: 'inventory.auto_reorder', value: false })
    expect(useSystem().settings[0].value).toBe(false)
  })

  it('asks before switching off the safety auto-pause, and does nothing when cancelled', async () => {
    const calls = serve(on('POST', '/api/v1/commands/config.set', () => json(202, { accepted: true })))
    useSystem().settings = [setting({})]
    const w = mount(SettingsPanel)
    await w.get('[role=switch]').trigger('click')
    const confirm = useConfirm()
    expect(confirm.current?.title).toBe('Turn off auto-pause?')
    confirm.answer(false)
    await flushPromises()
    expect(calls).toHaveLength(0)
    expect(useSystem().settings[0].value).toBe(true)
    expect(w.get('[role=switch]').attributes('aria-checked')).toBe('true')
  })

  it('puts the switch back when the command is refused', async () => {
    serve(on('POST', '/api/v1/commands/config.set', () => json(503, { detail: 'NATS unavailable' })))
    useSystem().settings = [setting({ key: 'inventory.auto_reorder', value: true })]
    const w = mount(SettingsPanel)
    await w.get('[role=switch]').trigger('click')
    await flushPromises()
    expect(useSystem().settings[0].value).toBe(true)
  })
})

describe('AgentsTable', () => {
  it('shows every agent with its health', () => {
    const agents: AgentRow[] = [
      { name: 'telemetry', label: 'Telemetry', detail: 'printers', tier: 'core', status: 'healthy', heartbeat_age_s: 1.2, version: '0.3.0', uptime_s: 60, host: 'a1', queue: 0 },
      { name: 'finance', label: 'Finance', detail: 'ledger', tier: 'core', status: 'down', heartbeat_age_s: null, version: null, uptime_s: null, host: null, queue: 12 },
      { name: 'social', label: 'Social Listening', detail: 'phase 10', tier: 'optional', status: 'planned', heartbeat_age_s: null, version: null, uptime_s: null, host: null, queue: null },
    ]
    const w = mount(AgentsTable, { props: { agents } })
    expect(w.get('[data-testid=agent-telemetry]').text()).toContain('Healthy')
    expect(w.get('[data-testid=agent-telemetry]').text()).toContain('1.2 s')
    expect(w.get('[data-testid=agent-finance]').text()).toContain('Down')
    expect(w.get('[data-testid=agent-social]').text()).toContain('Planned')
  })
})

const printer = (over: Partial<Printer> = {}): Printer => ({
  id: 'bambu-01',
  vendor: 'bambu',
  protocol: 'Bambu MQTT',
  model: 'p1s',
  simulated: true,
  bed_mm: [256, 256],
  nozzle_mm: 0.4,
  material: 'PLA',
  color: 'black',
  online: true,
  busy: true,
  bed_clear: false,
  updated_at: '2026-11-14T09:00:00Z',
  camera: true,
  state: {
    printer_id: 'bambu-01',
    vendor: 'bambu',
    status: 'printing',
    plate_id: 'plate-7',
    progress: 0.62,
    layer: 142,
    total_layers: 230,
    nozzle_temp: 219,
    nozzle_target: 220,
    bed_temp: 60,
    bed_target: 60,
    remaining_s: 2280,
    observed_at: '2026-11-14T09:00:00Z',
  },
  ...over,
})

describe('PrinterTile', () => {
  const stubs = { CameraView: { template: '<div class="cam-stub" />' } }

  it('shows the state from telemetry', () => {
    const w = mount(PrinterTile, { props: { printer: printer() }, global: { stubs } })
    expect(w.text()).toContain('Printing')
    expect(w.text()).toContain('62% · layer 142 / 230')
    expect(w.text()).toContain('ETA 38 m')
    expect(w.get('[role=progressbar]').attributes('aria-valuenow')).toBe('62')
  })

  it('pauses through the gateway', async () => {
    const calls = serve(on('POST', '/api/v1/commands/printers/bambu-01/pause', () => json(202, { accepted: true })))
    const w = mount(PrinterTile, { props: { printer: printer() }, global: { stubs } })
    await w.get('[aria-label="Pause bambu-01"]').trigger('click')
    await flushPromises()
    expect(calls).toHaveLength(1)
    expect(calls[0].headers['Idempotency-Key']).toBeTruthy()
  })

  it('offers Resume for a paused printer and nothing for an offline one', () => {
    const paused = printer({ state: { ...printer().state!, status: 'paused' } })
    expect(mount(PrinterTile, { props: { printer: paused }, global: { stubs } }).find('[aria-label="Resume bambu-01"]').exists()).toBe(true)
    const offline = mount(PrinterTile, { props: { printer: printer({ online: false }) }, global: { stubs } })
    expect(offline.text()).toContain('Offline')
    expect(offline.find('[aria-label="Pause bambu-01"]').exists()).toBe(false)
  })
})
