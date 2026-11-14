// Test helpers: a scripted fetch for the gateway's REST API.
import { vi } from 'vitest'

export interface Call {
  url: string
  method: string
  body: unknown
  headers: Record<string, string>
}

export type Route = (call: Call) => Response | Promise<Response> | undefined

export function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Stub global fetch: the first route that returns a Response answers; unknown URLs get a 404. */
export function serve(...routes: Route[]): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const call: Call = {
        url,
        method: init.method ?? 'GET',
        body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
        headers: (init.headers as Record<string, string>) ?? {},
      }
      calls.push(call)
      for (const r of routes) {
        const res = await r(call)
        if (res) return res
      }
      return json(404, { detail: `no route for ${call.method} ${url}` })
    }),
  )
  return calls
}

export const on =
  (method: string, path: string | RegExp, reply: (call: Call) => Response): Route =>
  (call) =>
    call.method === method && (typeof path === 'string' ? call.url === path : path.test(call.url)) ? reply(call) : undefined
