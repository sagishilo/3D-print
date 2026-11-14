// HTTP client for the gateway. Cookies carry the session (HttpOnly); every change sends the CSRF double-submit
// header; a 401 is answered once with a silent refresh (rotating refresh cookie) and a retry.
import type { CommandAccepted } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

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
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken()
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey

  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  })
  if (res.status === 401 && opts.retry !== false && !NO_REFRESH.has(path)) {
    if (await refreshSession()) return api<T>(path, { ...opts, retry: false })
    unauthorizedHandler?.()
  }
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
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
    if (typeof err.detail === 'string') return err.detail
    if (Array.isArray(err.detail)) {
      return err.detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(-1)[0] ?? ''}: ${d.msg}`).join('; ')
    }
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}
