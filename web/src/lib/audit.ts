// The audit log in plain words: subjects ("cmd.coordinator.create_order", "auth.login", "users.create") become short
// action names, outcomes a colour, and the payload a one-line summary.
import type { Tone } from './types'

export interface AuditEntry {
  id: number
  created_at: string
  actor: string
  subject: string
  outcome: string
  payload: Record<string, unknown>
}

const NAMES: Record<string, string> = {
  'auth.login': 'Sign in',
  'auth.logout': 'Sign out',
  'auth.refresh': 'Session refresh',
  'auth.password': 'Password change',
  'users.create': 'Add user',
  'users.update': 'Change user',
  'users.reset_password': 'Reset password',
  'dlq.replay': 'Replay dead letter',
  'dlq.discard': 'Discard dead letter',
  'printers.test_connection': 'Printer connection test',
  auth: 'Sign-ins',
  users: 'User management',
  dlq: 'Dead letters',
}

const AREAS: Record<string, string> = {
  coordinator: 'Orders',
  fleet: 'Fleet',
  telemetry: 'Printers',
  inventory: 'Inventory',
  finance: 'Finance',
  personalization: 'Catalog',
  social: 'Social',
}

const words = (s: string) => {
  const w = s.replace(/_/g, ' ')
  return w.charAt(0).toUpperCase() + w.slice(1)
}

/** "cmd.coordinator.create_order" -> "Orders: create order"; known subjects by name. */
export function auditAction(subject: string): string {
  if (NAMES[subject]) return NAMES[subject]
  const parts = subject.split('.')
  if (parts[0] === 'cmd' && parts.length >= 2) {
    const area = AREAS[parts[1]] ?? words(parts[1])
    return parts.length > 2 ? `${area}: ${parts.slice(2).join(' ').replace(/_/g, ' ')}` : area
  }
  return words(parts.join(' '))
}

export function auditTone(outcome: string): Tone {
  if (outcome === 'done' || outcome === 'published') return 'ok'
  if (outcome === 'pending' || outcome === 'duplicate') return 'idle'
  return 'danger'   // failed: …, refused, locked out
}

/** A short line from the payload: the fields a person recognises, never secrets (the gateway never logs them). */
export function auditDetail(e: AuditEntry): string {
  const p = e.payload ?? {}
  const keys = ['user', 'role', 'order_id', 'printer_id', 'sku', 'key', 'value', 'ip', 'reason', 'host', 'customer_name',
    'material', 'color', 'subject']
  return keys
    .filter((k) => p[k] !== undefined && p[k] !== '' && p[k] !== null)
    .slice(0, 4)
    .map((k) => `${k.replace(/_/g, ' ')}: ${typeof p[k] === 'object' ? JSON.stringify(p[k]) : String(p[k])}`)
    .join(' · ')
}
