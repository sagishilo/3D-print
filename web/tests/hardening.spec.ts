import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import HealthBanner from '@/components/shell/HealthBanner.vue'
import { api, ApiError, errorText, OFFLINE } from '@/lib/api'
import { type Health, healthProblems } from '@/lib/health'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const res = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })

const healthy: Health = {
  ok: true, nats: true, postgres: true, go2rtc: true,
  migrations: { ok: true, applied: 9, pending: [], problems: [], latest: '009_catalog.sql' },
}

describe('api() resilience', () => {
  it('retries a read once after a 503, honouring Retry-After', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn()
      .mockResolvedValueOnce(res(503, { detail: 'database unavailable; try again shortly' }, { 'Retry-After': '2' }))
      .mockResolvedValueOnce(res(200, { ok: 1 }))
    vi.stubGlobal('fetch', fetch)
    const p = api('/api/v1/orders')
    await vi.advanceTimersByTimeAsync(1_999)
    expect(fetch).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await expect(p).resolves.toEqual({ ok: 1 })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('does not retry changes, and gives up on a read after one retry', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn(async () => res(429, { detail: 'too many requests; retry in 9 s' }, { 'Retry-After': '9' }))
    vi.stubGlobal('fetch', fetch)
    await expect(api('/api/v1/commands/x', { method: 'POST', body: {} })).rejects.toMatchObject({ status: 429 })
    expect(fetch).toHaveBeenCalledTimes(1)
    const read = api('/api/v1/orders').catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(5_000)                        // the wait is capped at 5 s
    expect(((await read) as ApiError).status).toBe(429)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('names an unreachable server and shows the reference of a server fault', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    const err = (await api('/api/v1/orders').catch((e: unknown) => e)) as ApiError
    expect(err.status).toBe(OFFLINE)
    expect(errorText(err)).toBe('cannot reach the server; check the connection')
    vi.stubGlobal('fetch', vi.fn(async () => res(500, { detail: 'internal error', request_id: 'abc123def4567890' })))
    expect(errorText(await api('/api/v1/orders').catch((e: unknown) => e))).toBe('internal error (ref abc123def4567890)')
    vi.stubGlobal('fetch', vi.fn(async () => res(413, { detail: 'request body too large', request_id: 'r1' })))
    expect(errorText(await api('/api/v1/x', { method: 'POST', body: {} }).catch((e: unknown) => e))).toBe('request body too large')
  })
})

describe('health', () => {
  it('words what is wrong, worst first', () => {
    expect(healthProblems(healthy)).toEqual([])
    expect(healthProblems(null)[0]).toMatch(/not answering/)
    const bad = healthProblems({ ...healthy, ok: false, postgres: false, go2rtc: false,
      migrations: { ...healthy.migrations!, ok: false, pending: ['010_x.sql'], problems: ['003 was changed after it was applied'] } })
    expect(bad[0]).toMatch(/database is unreachable/)
    expect(bad).toContain('003 was changed after it was applied')
    expect(bad.some((p) => p.includes('1 migration(s) pending'))).toBe(true)
    expect(bad.at(-1)).toMatch(/camera relay/)
  })

  it('the banner stays hidden when healthy and appears when the gateway reports trouble', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res(200, healthy)))
    let w = mount(HealthBanner)
    await flushPromises()
    expect(w.find('[data-testid=health-banner]').exists()).toBe(false)
    w.unmount()
    vi.stubGlobal('fetch', vi.fn(async () => res(503, { ...healthy, ok: false, nats: false })))
    w = mount(HealthBanner)
    await flushPromises()
    expect(w.get('[data-testid=health-banner]').text()).toContain('message bus is down')
    expect(w.get('[data-testid=health-banner]').classes()).toContain('serious')
    w.unmount()
  })
})
