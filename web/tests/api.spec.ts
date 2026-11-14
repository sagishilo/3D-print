import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, command, errorText, onUnauthorized, printerCommand } from '@/lib/api'

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>

function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let calls: { url: string; init: RequestInit }[]
function serve(handler: Handler) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, init })
      return handler(url, init)
    }),
  )
}
const header = (i: number, name: string) => (calls[i].init.headers as Record<string, string> | undefined)?.[name]

beforeEach(() => {
  document.cookie = 'pf_csrf=tok%2B123'
})
afterEach(() => {
  vi.unstubAllGlobals()
  onUnauthorized(() => undefined)
})

describe('api()', () => {
  it('sends the CSRF double-submit header on changes only', async () => {
    serve(() => json(200, { ok: true }))
    await api('/api/v1/printers')
    await api('/api/v1/system/dlq/3/discard', { method: 'POST' })
    expect(header(0, 'X-CSRF-Token')).toBeUndefined()
    expect(header(1, 'X-CSRF-Token')).toBe('tok+123')
    expect(calls[1].init.credentials).toBe('same-origin')
  })

  it('refreshes the session once on a 401 and retries the request', async () => {
    let first = true
    serve((url) => {
      if (url === '/api/v1/auth/refresh') return json(200, { ok: true })
      if (first) {
        first = false
        return json(401, { detail: 'invalid or expired token' })
      }
      return json(200, { printers: [] })
    })
    await expect(api('/api/v1/printers')).resolves.toEqual({ printers: [] })
    expect(calls.map((c) => c.url)).toEqual(['/api/v1/printers', '/api/v1/auth/refresh', '/api/v1/printers'])
  })

  it('shares one refresh between requests that hit a 401 together', async () => {
    const retried = new Set<string>()
    serve((url) => {
      if (url === '/api/v1/auth/refresh') return json(200, {})
      if (!retried.has(url)) {
        retried.add(url)
        return json(401, { detail: 'expired' })
      }
      return json(200, { url })
    })
    await Promise.all([api('/api/v1/printers'), api('/api/v1/approvals'), api('/api/v1/system/agents')])
    expect(calls.filter((c) => c.url === '/api/v1/auth/refresh')).toHaveLength(1)
  })

  it('reports a lost session when the refresh fails', async () => {
    const lost = vi.fn()
    onUnauthorized(lost)
    serve(() => json(401, { detail: 'no refresh token' }))
    await expect(api('/api/v1/printers')).rejects.toMatchObject({ status: 401 })
    expect(lost).toHaveBeenCalledOnce()
  })

  it('does not refresh for a failed login', async () => {
    serve(() => json(401, { detail: 'wrong username or password; 2 attempts left' }))
    const err = await api('/api/v1/auth/login', { method: 'POST', body: { username: 'admin', password: 'x' } }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(errorText(err)).toBe('wrong username or password; 2 attempts left')
    expect(calls).toHaveLength(1)
  })

  it('turns validation errors into one readable line', () => {
    const err = new ApiError(422, [
      { loc: ['body', 'key'], msg: 'Field required' },
      { loc: ['body', 'value'], msg: 'Input should be a valid boolean' },
    ])
    expect(errorText(err)).toBe('key: Field required; value: Input should be a valid boolean')
  })
})

describe('commands', () => {
  it('sends an idempotency key with every command', async () => {
    serve(() => json(202, { accepted: true, command_id: 'c1' }))
    await command('config.set', { key: 'safety.auto_pause', value: false })
    await printerCommand('bambu-01', 'pause')
    expect(calls[0].url).toBe('/api/v1/commands/config.set')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ key: 'safety.auto_pause', value: false })
    expect(header(0, 'Idempotency-Key')).toMatch(/^[0-9a-f]{32}$/)
    expect(calls[1].url).toBe('/api/v1/commands/printers/bambu-01/pause')
    expect(header(1, 'Idempotency-Key')).not.toBe(header(0, 'Idempotency-Key'))
  })
})
