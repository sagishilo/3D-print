// W2-C: the Schedule screen (timeline lanes, why a printer is idle, the queue's hold / move / pin, missing filament),
// a printer's Maintenance tab (plate swap, tasks, Done, owners add tasks), Today's second action, the helpers and
// the live-feed lines.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import MaintenancePanel from '@/components/fleet/MaintenancePanel.vue'
import { describe as describeEvent } from '@/lib/events'
import { useConfirm } from '@/stores/confirm'
import { hourLabel, placeBlock, taskId } from '@/stores/schedule'
import ScheduleView from '@/views/ScheduleView.vue'

import { json, on, serve, signedInAs } from './helpers'

const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })
const NOW = '2026-11-20T08:00:00Z'
const later = (h: number) => new Date(new Date(NOW).getTime() + h * 3600_000).toISOString()

const SCHEDULE = {
  now: NOW, horizon_h: 24,
  window: { overnight_from: 18, quiet_enabled: true, quiet_from: 23, quiet_until: 7, long_job_hours: 4, quiet_now: false },
  lanes: [
    { printer_id: 'p1', material: 'PLA', color: 'black', online: true, available: true, why_not: '', auto_eject: true, plate_stack: 6,
      plates_left: 4, blocks: [
        { start: later(-0.5), end: later(1), running: true, note: 'printing', jobs: [{ job_id: 'r1', order_id: 'o-1', reason: '' }] },
        { start: later(1), end: later(3), running: false, note: '', jobs: [{ job_id: 'b', order_id: 'o-2', reason: 'your order (#1)' }] },
      ] },
    { printer_id: 'p2', material: 'PLA', color: 'white', online: false, available: false, why_not: 'offline', auto_eject: false,
      plate_stack: 0, plates_left: 0, blocks: [] },
  ],
  queue: [
    { job_id: 'b', order_id: 'o-2', product_type: 'nameplate', material: 'PLA', color: 'black', est_minutes: 120, priority: 0, due_by: null,
      hold: false, pinned_printer: null, rank: 1, planned_printer: 'p1', planned_start: later(1), reason: 'your order (#1)' },
    { job_id: 'h', order_id: 'o-3', product_type: 'nameplate', material: 'PLA', color: 'black', est_minutes: 30, priority: 0, due_by: null,
      hold: true, pinned_printer: null, rank: null, planned_printer: null, planned_start: null, reason: 'held' },
  ],
  unplaced: [{ material: 'PETG', color: 'red', jobs: ['g1', 'g2'], minutes: 150 }],
}

const MAINT = {
  printer_id: 'p1', counters: { print_hours: 12.5, plates: 30, kg: 1.2 },
  policy: { auto_eject: true, plate_stack: 6, plates_left: 0 },
  tasks: [
    { printer_id: 'p1', task_id: 'clean-plate', label: 'Clean the build plate', every_hours: null, every_plates: 25, every_days: null,
      block_when_overdue: true, last_done_at: null, state: 'overdue', used: 1.2, left: '5 plates', blocks: true },
    { printer_id: 'p1', task_id: 'nozzle-check', label: 'Check the nozzle', every_hours: 100, every_plates: null, every_days: null,
      block_when_overdue: false, last_done_at: '2026-11-19T08:00:00Z', state: 'ok', used: 0.1, left: '88 h', blocks: false },
  ],
  log: [{ task_id: 'clean-plate', label: 'Clean the build plate', done_by: 'dana', note: 'IPA', at: '2026-11-18T08:00:00Z' }],
}

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs('owner')
})
afterEach(() => vi.unstubAllGlobals())

describe('helpers', () => {
  it('place a block on the lane, clipped to it', () => {
    expect(placeBlock({ start: later(-1), end: later(6) }, NOW, 24)).toEqual({ left: 0, width: 25 })
    expect(placeBlock({ start: later(12), end: later(30) }, NOW, 24)).toEqual({ left: 50, width: 50 })
    expect(placeBlock({ start: later(25), end: later(26) }, NOW, 24)).toBeNull()
    expect(placeBlock({ start: later(-3), end: later(-1) }, NOW, 24)).toBeNull()
  })

  it('label hours and slug task ids', () => {
    expect(hourLabel(18)).toBe('18:00')
    expect(hourLabel(7.5)).toBe('07:30')
    expect(taskId('Clean the plate (IPA)!')).toBe('clean-the-plate-ipa')
    expect(taskId('נקה')).toMatch(/^task-/)
  })

  it('describe the new events in plain words', () => {
    const d = (type: string, data: object) => describeEvent({ type: `io.printfarm.${type}.v1`, data } as never)
    expect(d('fleet.queue.controlled', { job_id: 'j1', action: 'pin', printer_id: 'p1', by: 'dana' }).text).toBe('Job j1 pinned to p1 (dana)')
    const s = d('printer.failure.suspected', { printer_id: 'p1', score: 0.86, action_taken: 'suggested' })
    expect(s.text).toContain('86%')
    expect(s.safety).toBe(true)
    expect(d('fleet.maintenance.due', { printer_id: 'p1', label: 'Lube', overdue: true, blocks: true }).text)
      .toBe('p1: Lube overdue · no new plates until done')
  })
})

describe('the Schedule screen', () => {
  it('draws a lane per printer, says why one is idle, and lists the queue with its reasons', async () => {
    serve(on('GET', '/api/v1/schedule', () => json(200, SCHEDULE)))
    const w = mount(ScheduleView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="schedule-window"]').text()).toContain('Overnight from 18:00')
    expect(w.get('[data-testid="schedule-window"]').text()).toContain('Quiet hours 23:00–07:00')
    expect(w.findAll('[data-testid^="block-p1-"]')).toHaveLength(2)
    expect(w.get('[data-testid="block-p1-0"]').classes()).toContain('running')
    expect(w.get('[data-testid="lane-p1"]').text()).toContain('plate swap: 4 plates left')
    expect(w.get('[data-testid="why-not-p2"]').text()).toBe('offline')
    expect(w.get('[data-testid="lane-p2"]').text()).toContain('Nothing planned')
    expect(w.get('[data-testid="reason-b"]').text()).toBe('your order (#1)')
    expect(w.get('[data-testid="queue-row-b"]').text()).toContain('your order #1')
    expect(w.get('[data-testid="queue-row-h"]').text()).toContain('held')
    expect(w.get('[data-testid="hold-h"]').text()).toBe('Release')
    expect(w.get('[data-testid="unplaced-PETG-red"]').text()).toContain('PETG red: 2 job(s), 2.5 h of printing')
    // a hover card tells the jobs and why
    await w.get('[data-testid="block-p1-1"]').trigger('mouseenter')
    expect(w.get('[role="tooltip"]').text()).toContain('your order (#1)')
    // the same as a table
    await w.get('[data-testid="timeline-toggle"]').trigger('click')
    expect(w.get('[data-testid="timeline-table"]').text()).toContain('o-2 (your order (#1))')
  })

  it('holds, moves and pins jobs with commands', async () => {
    const calls = serve(on('GET', '/api/v1/schedule', () => json(200, SCHEDULE)),
      on('POST', '/api/v1/commands/jobs.control', () => json(202, { command_id: 'c' })))
    const w = mount(ScheduleView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    await w.get('[data-testid="hold-b"]').trigger('click')
    await w.get('[data-testid="hold-h"]').trigger('click')
    await w.get('[data-testid="top-h"]').trigger('click')
    await w.get('[data-testid="auto-b"]').trigger('click')
    await w.get('[data-testid="pin-b"]').setValue('p1')
    await w.get('[data-testid="pin-b"]').setValue('')
    await flushPromises()
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([
      { job_id: 'b', action: 'hold' }, { job_id: 'h', action: 'release' }, { job_id: 'h', action: 'top' },
      { job_id: 'b', action: 'auto' }, { job_id: 'b', action: 'pin', printer_id: 'p1' }, { job_id: 'b', action: 'unpin' },
    ])
    // only printers with the job's filament are offered
    expect(w.get('[data-testid="pin-b"]').findAll('option').map((o) => o.attributes('value'))).toEqual(['', 'p1'])
  })

  it('viewers see the plan but cannot change it', async () => {
    signedInAs('viewer')
    serve(on('GET', '/api/v1/schedule', () => json(200, SCHEDULE)))
    const w = mount(ScheduleView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="hold-b"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="pin-b"]').attributes('disabled')).toBeDefined()
  })
})

describe('the Maintenance tab', () => {
  it('shows counters, plate swap and task states; Done and Refill ask first; owners add tasks', async () => {
    const calls = serve(on('GET', '/api/v1/printers/p1/maintenance', () => json(200, MAINT)),
      on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const w = mount(MaintenancePanel, { props: { printerId: 'p1' } })
    await flushPromises()
    expect(w.get('[data-testid="maintenance-counters"]').text()).toContain('12.5 print hours')
    expect(w.get('[data-testid="plates-left"]').text()).toBe('0 of 6 plates left')
    expect(w.get('[data-testid="task-state-clean-plate"]').text()).toBe('Overdue · over by 5 plates')
    expect(w.get('[data-testid="task-clean-plate"]').text()).toContain('blocks new plates when overdue')
    expect(w.get('[data-testid="task-state-nozzle-check"]').text()).toBe('OK · 88 h left')
    expect(w.get('[data-testid="maintenance-log"]').text()).toContain('dana · IPA')
    await w.get('[data-testid="task-done-clean-plate"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="refill"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="task-label"]').setValue('Dry the AMS desiccant')
    await w.get('[data-testid="task-save"]').trigger('submit')
    expect(w.text()).toContain('Set at least one interval.')
    await w.get('[data-testid="task-days"]').setValue(30)
    await w.get('[data-testid="task-add"]').trigger('submit')
    await flushPromises()
    const sent = calls.filter((c) => c.method === 'POST').map((c) => [c.url.split('/').at(-1), c.body])
    expect(sent).toEqual([
      ['maintenance.done', { printer_id: 'p1', task_id: 'clean-plate', note: '' }],
      ['printers.policy', { printer_id: 'p1', refill: true }],
      ['maintenance.task', { printer_id: 'p1', task_id: 'dry-the-ams-desiccant', label: 'Dry the AMS desiccant', every_hours: null,
        every_plates: null, every_days: 30, block_when_overdue: false }],
    ])
  })

  it('operators do the tasks but do not add them', async () => {
    signedInAs('operator')
    serve(on('GET', '/api/v1/printers/p1/maintenance', () => json(200, MAINT)))
    const w = mount(MaintenancePanel, { props: { printerId: 'p1' } })
    await flushPromises()
    expect(w.find('[data-testid="task-add"]').exists()).toBe(false)
    expect(w.get('[data-testid="task-done-clean-plate"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-testid="task-archive-clean-plate"]').attributes('disabled')).toBeDefined()
  })
})
