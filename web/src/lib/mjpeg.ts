// MJPEG (multipart/x-mixed-replace) over fetch, so a camera tile knows when its last frame arrived and can tell a
// live stream from a stalled one - an <img src=".mjpeg"> can't. Frames are cut by the part's Content-Length when the
// sender gives one (go2rtc does), otherwise by the JPEG start/end markers.
import { refreshSession } from './api'

const SOI = [0xff, 0xd8]
const EOI = [0xff, 0xd9]
const MAX_BUFFER = 16 * 1024 * 1024
const latin1 = new TextDecoder('latin1')

function indexOf(buf: Uint8Array, pattern: number[], from = 0): number {
  for (let i = from; i <= buf.length - pattern.length; i++) {
    let ok = true
    for (let j = 0; j < pattern.length; j++) {
      if (buf[i + j] !== pattern[j]) {
        ok = false
        break
      }
    }
    if (ok) return i
  }
  return -1
}

function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length)
  out.set(a, 0)
  out.set(b, a.length)
  return out
}

export class MjpegParser {
  private buf: Uint8Array = new Uint8Array(0)

  /** Feed bytes as they arrive; returns the JPEG frames completed by them. */
  push(chunk: Uint8Array): Uint8Array[] {
    this.buf = concat(this.buf, chunk)
    const frames: Uint8Array[] = []
    for (;;) {
      const soi = indexOf(this.buf, SOI)
      if (soi < 0) break
      const headers = latin1.decode(this.buf.subarray(0, soi))
      const length = /content-length:\s*(\d+)/i.exec(headers)
      if (length) {
        const n = Number(length[1])
        if (this.buf.length < soi + n) break
        frames.push(this.buf.slice(soi, soi + n))
        this.buf = this.buf.slice(soi + n)
        continue
      }
      const eoi = indexOf(this.buf, EOI, soi + 2)
      if (eoi < 0) break
      frames.push(this.buf.slice(soi, eoi + 2))
      this.buf = this.buf.slice(eoi + 2)
    }
    if (this.buf.length > MAX_BUFFER) this.buf = new Uint8Array(0) // garbage, start over
    return frames
  }
}

export type CameraState = 'connecting' | 'live' | 'stalled' | 'error' | 'stopped'

export interface MjpegReaderOptions {
  stallMs?: number
  backoffMs?: number[]
  fetchImpl?: typeof fetch
}

/** Reads one camera stream, reconnecting with backoff; stops when told to (tab hidden, tile removed). */
export class MjpegReader {
  lastFrameAt = 0
  frames = 0
  state: CameraState = 'stopped'
  detail = ''
  private controller: AbortController | null = null
  private running = false
  private attempts = 0
  private readonly opts: Required<Omit<MjpegReaderOptions, 'fetchImpl'>> & { fetchImpl?: typeof fetch }

  constructor(
    private readonly url: string,
    private readonly onFrame: (jpeg: Blob) => void,
    private readonly onState: (state: CameraState, detail: string) => void,
    opts: MjpegReaderOptions = {},
  ) {
    this.opts = { stallMs: 8000, backoffMs: [1000, 2000, 5000, 10000], ...opts }
  }

  start(): void {
    if (this.running) return
    this.running = true
    void this.loop()
  }

  stop(): void {
    this.running = false
    this.controller?.abort()
    this.set('stopped', '')
  }

  private set(state: CameraState, detail: string): void {
    if (state === this.state && detail === this.detail) return
    this.state = state
    this.detail = detail
    this.onState(state, detail)
  }

  private async loop(): Promise<void> {
    while (this.running) {
      const controller = new AbortController()
      this.controller = controller
      let stalled = false
      const watchdog = setInterval(() => {
        const since = Date.now() - (this.lastFrameAt || Date.now())
        if (this.state === 'live' && since > this.opts.stallMs) {
          stalled = true
          controller.abort()
        }
      }, 1000)
      try {
        this.set('connecting', '')
        const fetchImpl = this.opts.fetchImpl ?? fetch
        let res = await fetchImpl(this.url, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
        if (res.status === 401 && (await refreshSession())) {
          res = await fetchImpl(this.url, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
        }
        if (!res.ok || !res.body) {
          let detail = `HTTP ${res.status}`
          try {
            detail = ((await res.json()) as { detail?: string }).detail ?? detail
          } catch {
            /* not JSON */
          }
          throw new Error(detail)
        }
        const reader = res.body.getReader()
        const parser = new MjpegParser()
        for (;;) {
          const { value, done } = await reader.read()
          if (done) throw new Error('stream ended')
          for (const frame of parser.push(value)) {
            this.lastFrameAt = Date.now()
            this.frames += 1
            this.attempts = 0
            this.set('live', '')
            this.onFrame(new Blob([frame as BlobPart], { type: 'image/jpeg' }))
          }
        }
      } catch (err) {
        if (!this.running) break
        const message = stalled ? 'no frame for a while' : err instanceof Error ? err.message : String(err)
        this.set(stalled ? 'stalled' : 'error', message)
        const delays = this.opts.backoffMs
        await new Promise((r) => setTimeout(r, delays[Math.min(this.attempts++, delays.length - 1)]))
      } finally {
        clearInterval(watchdog)
      }
    }
  }
}
