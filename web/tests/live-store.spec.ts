import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WebSocketLike } from '@/lib/live'
import { useLive } from '@/stores/live'
import { usePrinters } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'

import { json, on, serve } from './helpers'

// The store opens a real WebSocket; replace it with a scripted one.
class FakeSocket implements WebSocketLike {
  static last: FakeSocket
  sent: Record<string, any>[] = []
  onopen: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: ((ev: { code: number }) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  constructor() {
    FakeSocket.last = this
  }
  send(d: string) {
    this.sent.push(JSON.parse(d))
  }
  close() {}
  receive(msg: object) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
}

let seq = 0
const msg = (type: string, data: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  type: 'event',
  topic: `evt.${type}`,
  stream: 'EVENTS',
  seq: ++seq,
  data: { id: `id-${seq}`, type: `io.printfarm.${type}.v1`, source: 'test', time: new Date(Date.now() + seq).toISOString(), kind: 'event', correlationid: 'c', data },
  ...extra,
})

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  vi.stubGlobal('WebSocket', FakeSocket)
  serve(on('GET', '/api/v1/printers', () => json(200, { printers: [] })), () => json(200, {}))
})
afterEach(() => {
  useLive().stop()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('live store', () => {
  it('merges events newest first, once each, in batches', () => {
    const live = useLive()
    live.start()
    const a = msg('fleet.job.queued', { job_id: 'j1' })
    FakeSocket.last.receive(a)
    FakeSocket.last.receive(msg('fleet.job.queued', { job_id: 'j2' }))
    FakeSocket.last.receive({ ...a, type: 'replay', id: 'r1' }) // the same event again from a replay
    expect(live.feed).toHaveLength(0) // not rendered yet
    vi.advanceTimersByTime(60)
    expect(live.feed.map((e) => e.d.text)).toEqual(['Job j2 queued', 'Job j1 queued'])
  })

  it('applies telemetry to the printer instead of the feed', () => {
    const live = useLive()
    const printers = usePrinters()
    printers.list = [{ id: 'voron-01', online: false, state: null } as never]
    live.start()
    FakeSocket.last.receive({ ...msg('x', {}), topic: 'tlm.voron-01.state', stream: 'TELEMETRY', data: { id: 't1', type: 'io.printfarm.printer.state.v1', data: { printer_id: 'voron-01', status: 'printing', progress: 0.5, observed_at: 'now' } } })
    vi.advanceTimersByTime(60)
    expect(printers.get('voron-01')?.online).toBe(true)
    expect(printers.get('voron-01')?.state?.progress).toBe(0.5)
    expect(live.feed).toHaveLength(0)
  })

  it('raises a sticky safety toast and a banner alert for an unverified pause', () => {
    const live = useLive()
    live.start()
    FakeSocket.last.receive(msg('printer.pause.unconfirmed', { printer_id: 'bambu-02', attempts: 2, last_status: 'printing', online: true, reason: 'thermal runaway' }))
    vi.advanceTimersByTime(60)
    const toast = useToasts().items[0]
    expect(toast).toMatchObject({ tone: 'danger', sticky: true })
    expect(live.alerts).toHaveLength(1)
    live.acknowledge(live.alerts[0].key)
    expect(live.alerts).toHaveLength(0)
  })

  it('does not alert for history that is only replayed', () => {
    const live = useLive()
    live.start()
    FakeSocket.last.receive({ ...msg('printer.pause.unconfirmed', { printer_id: 'bambu-02', attempts: 2 }), type: 'replay', id: 'r1' })
    vi.advanceTimersByTime(60)
    expect(live.feed).toHaveLength(1)
    expect(live.alerts).toHaveLength(0)
    expect(useToasts().items).toHaveLength(0)
  })

  it('shows how long ago the last message came in', () => {
    const live = useLive()
    live.start()
    FakeSocket.last.onopen?.({})
    FakeSocket.last.receive({ type: 'subscribed', resumed: {} })
    vi.advanceTimersByTime(2000)
    expect(live.label).toMatch(/^Live · 2\.\d s ago$/)
    expect(live.tone).toBe('ok')
    vi.advanceTimersByTime(31_000)
    expect(live.tone).toBe('warn') // connected but silent: no heartbeat for 30 s
  })
})
