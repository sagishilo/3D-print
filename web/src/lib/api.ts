// HTTP client for the gateway. Cookies carry the session (HttpOnly); every change sends the CSRF double-submit
// header; a 401 is answered once with a silent refresh (rotating refresh cookie) and a retry.
// A read (GET) that meets a brief outage (503) or the rate limit (429) is retried once after Retry-After (at most
// 5 s); changes are not retried here (commands carry an Idempotency-Key, so the operator can safely send again).
import type { CommandAccepted } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown
  readonly requestId: string

  constructor(status: number, detail: unknown, requestId = '') {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`)
    this.status = status
    this.detail = detail
    this.requestId = requestId
  }
}

/** Status 0: the request never got an answer (network down, gateway restarting). */
export const OFFLINE = 0
const RETRY_STATUSES = new Set([429, 503])
const MAX_RETRY_WAIT_MS = 5_000

function retryWait(res: Response): number {
  const s = Number(res.headers.get('Retry-After'))
  return Number.isFinite(s) && s > 0 ? Math.min(s * 1000, MAX_RETRY_WAIT_MS) : 1_000
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

let unauthorizedHandler: (() => void) | null = null
let refreshing: Promise<boolean> | null = null
const NO_REFRESH = new Set(['/api/v1/auth/login', '/api/v1/auth/refresh', '/api/v1/auth/logout'])

/** Called when the session is gone for good (refresh failed): the auth store sends the user to the login page. */
export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler
}

export function csrfToken(): string {
  const match = document.cookie.split('; ').find((c) => c.startsWith('pf_csrf='))
  return match ? decodeURIComponent(match.slice('pf_csrf='.length)) : ''
}

/** One refresh at a time, shared by every request that hit a 401 meanwhile. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'same-origin' })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  idempotencyKey?: string
  retry?: boolean
  /** internal: the read was already retried once after a 503/429 */
  retried?: boolean
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken()
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey

  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    })
  } catch {
    throw new ApiError(OFFLINE, 'cannot reach the server; check the connection')
  }
  if (RETRY_STATUSES.has(res.status) && method === 'GET' && !opts.retried) {
    await sleep(retryWait(res))
    return api<T>(path, { ...opts, retried: true })
  }
  if (res.status === 401 && opts.retry !== false && !NO_REFRESH.has(path)) {
    if (await refreshSession()) return api<T>(path, { ...opts, retry: false })
    unauthorizedHandler?.()
  }
  if (!res.ok) {
    let detail: unknown = res.statusText
    let requestId = res.headers.get('X-Request-ID') ?? ''
    try {
      const body = (await res.json()) as { detail?: unknown; request_id?: string }
      detail = body.detail ?? detail
      requestId = body.request_id ?? requestId
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail, requestId)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID().replaceAll('-', '')
}

/** Send a command (gateway/app/commands.py); the result arrives later on the live feed. */
export function command(name: string, body: Record<string, unknown> = {}, key = newIdempotencyKey()) {
  return api<CommandAccepted>(`/api/v1/commands/${name}`, { method: 'POST', body, idempotencyKey: key })
}

export function printerCommand(printerId: string, action: 'pause' | 'resume' | 'cancel', body: Record<string, unknown> = {}) {
  return api<CommandAccepted>(`/api/v1/commands/printers/${encodeURIComponent(printerId)}/${action}`, {
    method: 'POST',
    body,
    idempotencyKey: newIdempotencyKey(),
  })
}

/** A readable message for any error a request can throw. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    // a server fault: the reference finds it in the gateway log (docker compose logs gateway)
    if (err.status >= 500 && err.requestId && typeof err.detail === 'string') return `${err.detail} (ref ${err.requestId})`
    if (typeof err.detail === 'string') return err.detail
    if (Array.isArray(err.detail)) {
      return err.detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(-1)[0] ?? ''}: ${d.msg}`).join('; ')
    }
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}
