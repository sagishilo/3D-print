import { afterEach, describe, expect, it, vi } from 'vitest'

import { type CameraState, MjpegParser, MjpegReader } from '@/lib/mjpeg'

const enc = new TextEncoder()
const jpeg = (n: number) => new Uint8Array([0xff, 0xd8, ...Array.from({ length: n }, (_, i) => i % 200), 0xff, 0xd9])

function part(frame: Uint8Array, withLength = true): Uint8Array {
  const head = enc.encode(`--frame\r\nContent-Type: image/jpeg\r\n${withLength ? `Content-Length: ${frame.length}\r\n` : ''}\r\n`)
  const out = new Uint8Array(head.length + frame.length + 2)
  out.set(head, 0)
  out.set(frame, head.length)
  out.set(enc.encode('\r\n'), head.length + frame.length)
  return out
}

function join(...chunks: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0))
  let at = 0
  for (const c of chunks) {
    out.set(c, at)
    at += c.length
  }
  return out
}

describe('MjpegParser', () => {
  it('cuts frames by Content-Length, also across chunk boundaries', () => {
    const a = jpeg(500)
    const b = jpeg(300)
    const stream = join(part(a), part(b))
    const p = new MjpegParser()
    expect(p.push(stream.subarray(0, 200))).toHaveLength(0)
    const first = p.push(stream.subarray(200, 600))
    expect(first).toHaveLength(1)
    expect(first[0]).toEqual(a)
    const rest = p.push(stream.subarray(600))
    expect(rest).toHaveLength(1)
    expect(rest[0]).toEqual(b)
  })

  it('falls back to the JPEG markers without a Content-Length', () => {
    const a = jpeg(64)
    const b = jpeg(32)
    const frames = new MjpegParser().push(join(part(a, false), part(b, false)))
    expect(frames).toEqual([a, b])
  })

  it('keeps an unfinished frame until its end arrives', () => {
    const a = jpeg(100)
    const p = new MjpegParser()
    const bytes = part(a, false)
    expect(p.push(bytes.subarray(0, bytes.length - 4))).toEqual([])
    expect(p.push(bytes.subarray(bytes.length - 4))).toEqual([a])
  })
})

function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(c)
      controller.close()
    },
  })
}

describe('MjpegReader', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reports frames and goes live, then reconnects when the stream ends', async () => {
    const states: CameraState[] = []
    const frames: Blob[] = []
    let requests = 0
    const fetchImpl = vi.fn(async () => {
      requests += 1
      return new Response(streamOf([part(jpeg(50)), part(jpeg(60))]), { status: 200 })
    }) as unknown as typeof fetch
    const reader = new MjpegReader('/api/v1/cameras/bambu-01/mjpeg', (b) => frames.push(b), (s) => states.push(s), {
      fetchImpl,
      backoffMs: [5],
    })
    reader.start()
    await vi.waitFor(() => expect(requests).toBeGreaterThanOrEqual(2))
    reader.stop()
    expect(frames.length).toBeGreaterThanOrEqual(2)
    expect(frames[0].type).toBe('image/jpeg')
    expect(states.slice(0, 3)).toEqual(['connecting', 'live', 'error'])
    expect(states[states.length - 1]).toBe('stopped')
  })

  it('shows the gateway error and retries after a refreshed session on 401', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 }))) // POST /api/v1/auth/refresh
    const details: string[] = []
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response('{"detail":"not authenticated"}', { status: 401 }))
      .mockResolvedValue(new Response('{"detail":"camera unreachable"}', { status: 502 })) as unknown as typeof fetch
    const reader = new MjpegReader('/cam', () => undefined, (s, d) => s === 'error' && details.push(d), {
      fetchImpl,
      backoffMs: [1000],
    })
    reader.start()
    await vi.waitFor(() => expect(details).toEqual(['camera unreachable']))
    reader.stop()
    expect(fetch).toHaveBeenCalledWith('/api/v1/auth/refresh', expect.objectContaining({ method: 'POST' }))
  })
})
