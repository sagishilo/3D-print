import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LiveClient, type LiveOptions, type WebSocketLike } from '@/lib/live'
import type { FeedItem } from '@/lib/types'

class FakeSocket implements WebSocketLike {
  sent: Record<string, any>[] = []
  closedWith: number | null = null
  onopen: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: ((ev: { code: number }) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null

  constructor(readonly url: string) {}
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  close(code?: number) {
    this.closedWith = code ?? 1000
  }
  open() {
    this.onopen?.({})
  }
  receive(msg: object) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
  drop(code: number) {
    this.onclose?.({ code })
  }
}

class MemoryStorage {
  data = new Map<string, string>()
  getItem(k: string) {
    return this.data.get(k) ?? null
  }
  setItem(k: string, v: string) {
    this.data.set(k, v)
  }
  removeItem(k: string) {
    this.data.delete(k)
  }
}

const event = (stream: string, seq: number, id: string, topic = 'evt.printer.print.started'): FeedItem => ({
  type: 'event',
  topic,
  stream,
  seq,
  data: { id, type: 'io.printfarm.printer.print.started.v1', source: 'telemetry', time: '2026-11-14T09:00:00Z', kind: 'event', correlationid: id, data: {} },
})

let sockets: FakeSocket[]
let storage: MemoryStorage
let items: FeedItem[]

function client(extra: Partial<LiveOptions> = {}) {
  return new LiveClient({
    topics: ['evt.>', 'tlm.*.state', 'dlq.>'],
    refresh: async () => true,
    onItem: (i) => items.push(i),
    url: 'wss://farm.local/ws',
    socketFactory: (url) => {
      const s = new FakeSocket(url)
      sockets.push(s)
      return s
    },
    storage,
    backoffMs: [1000, 2000],
    ...extra,
  })
}
const last = () => sockets[sockets.length - 1]

beforeEach(() => {
  vi.useFakeTimers()
  sockets = []
  items = []
  storage = new MemoryStorage()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('LiveClient', () => {
  it('subscribes with the cursors saved by an earlier page load', () => {
    storage.setItem('printfarm.live.cursors', JSON.stringify({ EVENTS: 41, DLQ: 3, junk: 'x' }))
    client().start()
    last().open()
    expect(last().sent[0]).toEqual({ op: 'sub', topics: ['evt.>', 'tlm.*.state', 'dlq.>'], resume: { EVENTS: 41, DLQ: 3 } })
  })

  it('goes live on "subscribed" and advances the cursor for stored streams only', () => {
    const statuses: string[] = []
    const c = client({ onStatus: (s) => statuses.push(s) })
    c.start()
    last().open()
    last().receive({ type: 'subscribed', topics: ['evt.>'], refused: [], resumed: {} })
    last().receive(event('EVENTS', 42, 'a'))
    last().receive(event('TELEMETRY', 900, 'b', 'tlm.bambu-01.state'))
    expect(statuses).toEqual(['connecting', 'live'])
    expect(items.map((i) => i.seq)).toEqual([42, 900])
    expect(c.cursors).toEqual({ EVENTS: 42 })
    expect(JSON.parse(storage.getItem('printfarm.live.cursors')!)).toEqual({ EVENTS: 42 })
  })

  it('drops an event it already delivered (overlap after a reconnect)', () => {
    client().start()
    last().open()
    last().receive(event('EVENTS', 5, 'same'))
    last().receive(event('EVENTS', 5, 'same'))
    expect(items).toHaveLength(1)
  })

  it('reconnects with backoff and resumes after the last event it saw', () => {
    const c = client()
    c.start()
    last().open()
    last().receive(event('EVENTS', 7, 'e7'))
    last().drop(1006)
    expect(c.status).toBe('reconnecting')
    vi.advanceTimersByTime(999)
    expect(sockets).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(sockets).toHaveLength(2)
    last().open()
    expect(last().sent[0].resume).toEqual({ EVENTS: 7 })
    last().drop(1006)
    vi.advanceTimersByTime(1999)
    expect(sockets).toHaveLength(2) // second delay is longer
    vi.advanceTimersByTime(1)
    expect(sockets).toHaveLength(3)
  })

  it('renews the session after 4401 and reconnects', async () => {
    const refresh = vi.fn(async () => true)
    client({ refresh }).start()
    last().drop(4401)
    await vi.runAllTimersAsync()
    expect(refresh).toHaveBeenCalledOnce()
    expect(sockets).toHaveLength(2)
  })

  it('stops and reports the lost session when the renewal fails', async () => {
    const onUnauthorized = vi.fn()
    const c = client({ refresh: async () => false, onUnauthorized })
    c.start()
    last().drop(4401)
    await vi.runAllTimersAsync()
    expect(onUnauthorized).toHaveBeenCalledOnce()
    expect(c.status).toBe('stopped')
    expect(sockets).toHaveLength(1)
  })

  it('does not retry when the origin is refused (4403)', async () => {
    const c = client()
    c.start()
    last().drop(4403)
    await vi.runAllTimersAsync()
    expect(c.status).toBe('blocked')
    expect(sockets).toHaveLength(1)
  })

  it('reports a gap and forgets a cursor the server no longer knows', () => {
    storage.setItem('printfarm.live.cursors', JSON.stringify({ EVENTS: 10, DLQ: 99 }))
    const gaps: unknown[] = []
    const c = client({ onGap: (g) => gaps.push(g) })
    c.start()
    last().open()
    last().receive({
      type: 'subscribed',
      resumed: { EVENTS: { from: 8001, gap: 9990, truncated: true }, DLQ: { reset: true } },
    })
    expect(gaps).toEqual([
      { stream: 'EVENTS', from: 8001, gap: 9990, truncated: true },
      { stream: 'DLQ', reset: true },
    ])
    expect(c.cursors).toEqual({ EVENTS: 10 })
  })

  it('replays stored history without moving the live cursor', () => {
    const ends: unknown[] = []
    const c = client({ onReplayEnd: (id, info) => ends.push({ id, ...info }) })
    c.start()
    last().open()
    const id = c.replay(['evt.printer.>'], new Date('2026-11-14T08:00:00Z'), 100)
    expect(last().sent[1]).toEqual({ op: 'replay', id: 'r1', topics: ['evt.printer.>'], since: '2026-11-14T08:00:00.000Z', limit: 100 })
    last().receive({ ...event('EVENTS', 3, 'old'), type: 'replay', id })
    last().receive({ type: 'replay_end', id, count: 1, truncated: false })
    expect(items[0].type).toBe('replay')
    expect(c.cursors).toEqual({})
    expect(ends).toEqual([{ id: 'r1', count: 1, truncated: false, error: undefined }])
  })

  it('stop() closes the socket and never reconnects', async () => {
    const c = client()
    c.start()
    const s = last()
    c.stop()
    expect(s.closedWith).toBe(1000)
    s.drop(1000)
    await vi.runAllTimersAsync()
    expect(sockets).toHaveLength(1)
    expect(c.status).toBe('stopped')
  })
})
