import { describe, expect, it } from 'vitest'

import { describe as describeEvent, kindOf } from '@/lib/events'
import type { Envelope } from '@/lib/types'

const env = (type: string, data: Record<string, unknown>): Envelope => ({
  id: 'x',
  source: 'telemetry',
  type,
  time: '2026-11-14T09:00:00Z',
  kind: 'event',
  correlationid: 'x',
  data,
})

describe('events', () => {
  it('derives the kind from the CloudEvents type', () => {
    expect(kindOf('io.printfarm.printer.pause.confirmed.v1')).toBe('printer.pause.confirmed')
    expect(kindOf('io.printfarm.fleet.plate.finished.v2')).toBe('fleet.plate.finished')
  })

  it('marks an unconfirmed pause as a safety alert', () => {
    const d = describeEvent(
      env('io.printfarm.printer.pause.unconfirmed.v1', {
        printer_id: 'bambu-02',
        attempts: 2,
        last_status: 'printing',
        online: true,
        reason: 'thermal runaway',
      }),
    )
    expect(d).toMatchObject({ safety: true, tone: 'danger', category: 'printer', printerId: 'bambu-02' })
    expect(d.text).toContain('did NOT pause after 2 attempt(s): still printing')
  })

  it('describes a confirmed pause with its latency', () => {
    const d = describeEvent(
      env('io.printfarm.printer.pause.confirmed.v1', { printer_id: 'voron-01', latency_ms: 1840, reason: 'operator', requested_by: 'admin' }),
    )
    expect(d.text).toBe('voron-01 paused in 1.8 s · operator (admin)')
    expect(d.tone).toBe('ok')
  })

  it('maps failure severity to a tone', () => {
    const base = { printer_id: 'bambu-01', detector: 'thermal_runaway', message: 'nozzle 300 °C', action_taken: 'paused' }
    expect(describeEvent(env('io.printfarm.printer.failure.detected.v1', { ...base, severity: 'critical' })).tone).toBe('danger')
    expect(describeEvent(env('io.printfarm.printer.failure.detected.v1', { ...base, severity: 'warning' })).tone).toBe('warn')
  })

  it('formats money in shekels from agorot', () => {
    const d = describeEvent(env('io.printfarm.order.payment.recorded.v1', { amount_agorot: 12_345, method: 'bit', order_id: 'o-1' }))
    expect(d.text).toBe('Payment ₪123.45 (bit) for o-1')
    expect(d.category).toBe('money')
  })

  it('falls back to the kind for unknown events', () => {
    const d = describeEvent(env('io.printfarm.something.new.v1', {}))
    expect(d).toMatchObject({ kind: 'something.new', text: 'something.new', category: 'system', tone: 'idle' })
  })
})
