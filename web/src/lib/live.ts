// Live feed client for the gateway's /ws (gateway/app/live.py).
//
// Every event carries its JetStream stream and sequence. The client keeps the last sequence per stream (for this
// browser tab) and sends it on every (re)connect, so the gateway resumes exactly after the last event this tab saw:
// nothing is missed across a dropped Wi-Fi or a page reload. Telemetry is not resumed: only the newest printer state
// matters, and the REST snapshot provides it.
import type { FeedItem } from './types'

export type LiveStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'blocked' | 'stopped'

export interface WebSocketLike {
  send(data: string): void
  close(code?: number): void
  onopen: ((ev: unknown) => void) | null
  onmessage: ((ev: { data: string }) => void) | null
  onclose: ((ev: { code: number }) => void) | null
  onerror: ((ev: unknown) => void) | null
}

export interface GapInfo {
  stream: string
  reset?: boolean
  truncated?: boolean
  gap?: number
  from?: number
}

export interface ReplayEnd {
  count: number
  truncated: boolean
  error?: string
}

export interface LiveOptions {
  topics: string[]
  /** Renew the session after the server closed with 4401; false = logged out. */
  refresh: () => Promise<boolean>
  onItem: (item: FeedItem) => void
  onStatus?: (status: LiveStatus) => void
  onGap?: (info: GapInfo) => void
  onReplayEnd?: (id: string, info: ReplayEnd) => void
  onUnauthorized?: () => void
  url?: string
  socketFactory?: (url: string) => WebSocketLike
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null
  resumeStreams?: string[]
  backoffMs?: number[]
  now?: () => number
}

const CURSOR_KEY = 'printfarm.live.cursors'
const SEEN_MAX = 5000

function defaultUrl(): string {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
}

export class LiveClient {
  status: LiveStatus = 'idle'
  lastMessageAt = 0
  readonly cursors: Record<string, number>
  private ws: WebSocketLike | null = null
  private stopped = true
  private attempts = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private replayCounter = 0
  private readonly seen = new Set<string>()
  private readonly seenOrder: string[] = []
  private readonly opts: LiveOptions
  private readonly resumeStreams: Set<string>

  constructor(opts: LiveOptions) {
    this.opts = opts
    this.resumeStreams = new Set(opts.resumeStreams ?? ['EVENTS', 'DLQ'])
    this.cursors = this.loadCursors()
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.connect(false)
  }

  stop(): void {
    this.stopped = true
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    const ws = this.ws
    this.ws = null
    ws?.close(1000)
    this.setStatus('stopped')
  }

  /** Ask for stored history since ``since``; items arrive through onItem with type "replay", then onReplayEnd. */
  replay(topics: string[], since: Date, limit = 500): string {
    const id = `r${++this.replayCounter}`
    this.send({ op: 'replay', id, topics, since: since.toISOString(), limit })
    return id
  }

  private now(): number {
    return this.opts.now?.() ?? Date.now()
  }

  private setStatus(status: LiveStatus): void {
    if (this.status === status) return
    this.status = status
    this.opts.onStatus?.(status)
  }

  private send(msg: object): void {
    this.ws?.send(JSON.stringify(msg))
  }

  private connect(reconnecting: boolean): void {
    this.setStatus(reconnecting ? 'reconnecting' : 'connecting')
    const factory = this.opts.socketFactory ?? ((url: string) => new WebSocket(url) as unknown as WebSocketLike)
    const ws = factory(this.opts.url ?? defaultUrl())
    this.ws = ws
    ws.onopen = () => ws.send(JSON.stringify({ op: 'sub', topics: this.opts.topics, resume: { ...this.cursors } }))
    ws.onmessage = (ev) => this.handle(ev.data)
    ws.onclose = (ev) => {
      if (this.ws === ws) this.closed(ev.code)
    }
    ws.onerror = () => {
      /* onclose follows */
    }
  }

  private handle(raw: string): void {
    let msg: Record<string, any>
    try {
      msg = JSON.parse(raw)
    } catch {
      return
    }
    this.lastMessageAt = this.now()
    switch (msg.type) {
      case 'subscribed':
        this.attempts = 0
        for (const [stream, info] of Object.entries((msg.resumed ?? {}) as Record<string, Omit<GapInfo, 'stream'>>)) {
          if (info.reset) {
            delete this.cursors[stream]
            this.saveCursors()
          }
          if (info.reset || info.truncated) this.opts.onGap?.({ stream, ...info })
        }
        this.setStatus('live')
        break
      case 'event': {
        const id = msg.data?.id as string | undefined
        if (id && this.markSeen(id)) return // already delivered (overlap after a reconnect)
        this.advance(msg.stream, msg.seq)
        this.opts.onItem(msg as FeedItem)
        break
      }
      case 'replay':
        this.opts.onItem(msg as FeedItem)
        break
      case 'replay_end':
        this.opts.onReplayEnd?.(msg.id, { count: msg.count, truncated: msg.truncated, error: msg.error })
        break
      default: // "hb": the connection is alive; lastMessageAt is enough
    }
  }

  private closed(code: number): void {
    this.ws = null
    if (this.stopped) return
    if (code === 4403) {
      this.setStatus('blocked') // page not served from an allowed origin: retrying cannot help
      return
    }
    if (code === 4401) {
      this.setStatus('reconnecting')
      void this.opts.refresh().then((ok) => {
        if (this.stopped) return
        if (ok) this.connect(true)
        else {
          this.stop()
          this.opts.onUnauthorized?.()
        }
      })
      return
    }
    const delays = this.opts.backoffMs ?? [1000, 2000, 5000, 10000, 15000]
    const delay = delays[Math.min(this.attempts, delays.length - 1)]
    this.attempts += 1
    this.setStatus('reconnecting')
    this.timer = setTimeout(() => {
      this.timer = null
      if (!this.stopped) this.connect(true)
    }, delay)
  }

  private markSeen(id: string): boolean {
    if (this.seen.has(id)) return true
    this.seen.add(id)
    this.seenOrder.push(id)
    if (this.seenOrder.length > SEEN_MAX) this.seen.delete(this.seenOrder.shift()!)
    return false
  }

  private advance(stream: string, seq: number): void {
    if (!this.resumeStreams.has(stream) || typeof seq !== 'number') return
    if (seq > (this.cursors[stream] ?? 0)) {
      this.cursors[stream] = seq
      this.saveCursors()
    }
  }

  private storage() {
    if (this.opts.storage !== undefined) return this.opts.storage
    try {
      return window.sessionStorage
    } catch {
      return null
    }
  }

  private loadCursors(): Record<string, number> {
    try {
      const raw = this.storage()?.getItem(CURSOR_KEY)
      const parsed = raw ? JSON.parse(raw) : {}
      return Object.fromEntries(Object.entries(parsed).filter(([, v]) => typeof v === 'number')) as Record<string, number>
    } catch {
      return {}
    }
  }

  private saveCursors(): void {
    try {
      this.storage()?.setItem(CURSOR_KEY, JSON.stringify(this.cursors))
    } catch {
      /* private mode / storage full: resume within this page still works */
    }
  }
}
