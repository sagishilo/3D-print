// Printer Fleet logic without UI: which printers a batch action applies to (and why the others are skipped),
// and plain-language readings of hardware values.
import type { Printer, PrinterHardware, PrinterStatus, Severity } from './types'

/** What a tile shows: the printer's status, plus "heating" (printing at 0 % while far from the targets). */
export type DisplayStatus = PrinterStatus | 'heating'

export type BatchAction = 'pause' | 'resume' | 'cancel' | 'clear_bed' | 'home'

export interface BatchPlan {
  action: BatchAction
  eligible: Printer[]
  skipped: { printer: Printer; reason: string }[]
}

export const BATCH_LABEL: Record<BatchAction, string> = {
  pause: 'Pause',
  resume: 'Resume',
  cancel: 'Cancel print',
  clear_bed: 'Bed cleared',
  home: 'Home axes',
}

/** Why ``action`` does not apply to a printer in this state (null = it applies). */
export function batchRefusal(action: BatchAction, p: Printer, status: DisplayStatus): string | null {
  if (status === 'offline') return 'offline'
  switch (action) {
    case 'pause':
      return status === 'printing' || status === 'heating' ? null : `not printing (${status})`
    case 'resume':
      return status === 'paused' ? null : `not paused (${status})`
    case 'cancel':
      return ['printing', 'heating', 'paused'].includes(status) ? null : `no print running (${status})`
    case 'clear_bed':
      if (p.bed_clear) return 'bed already marked clear'
      return ['printing', 'heating', 'paused', 'calibrating'].includes(status) ? `busy (${status})` : null
    case 'home':
      if (p.calibration || status === 'calibrating') return 'calibrating'
      return ['idle', 'finished', 'failed'].includes(status) ? null : `busy (${status})`
  }
}

export function planBatch(action: BatchAction, printers: Printer[], statusOf: (p: Printer) => DisplayStatus): BatchPlan {
  const plan: BatchPlan = { action, eligible: [], skipped: [] }
  for (const p of printers) {
    const reason = batchRefusal(action, p, statusOf(p))
    if (reason) plan.skipped.push({ printer: p, reason })
    else plan.eligible.push(p)
  }
  return plan
}

/** "2 printers: bambu-01, voron-01 · skipped: bambu-02 (not printing (idle))" */
export function describePlan(plan: BatchPlan): string {
  const names = plan.eligible.map((p) => p.id).join(', ')
  const head = `${plan.eligible.length} printer${plan.eligible.length === 1 ? '' : 's'}${names ? `: ${names}` : ''}`
  if (!plan.skipped.length) return head
  return `${head} · skipped: ${plan.skipped.map((s) => `${s.printer.id} (${s.reason})`).join(', ')}`
}

export function wifiQuality(dbm: number | null | undefined): { label: string; tone: 'ok' | 'warn' | 'danger' | 'idle' } {
  if (dbm == null) return { label: '—', tone: 'idle' }
  if (dbm >= -60) return { label: 'good', tone: 'ok' }
  if (dbm >= -70) return { label: 'fair', tone: 'warn' }
  return { label: 'weak', tone: 'danger' }
}

const FAN_LABEL: Record<string, string> = { part: 'Part cooling', aux: 'Auxiliary', chamber: 'Chamber', hotend: 'Hotend' }

export function fanRows(hw: PrinterHardware | null | undefined): { key: string; label: string; pct: number }[] {
  if (!hw) return []
  return Object.entries(hw.fans).map(([key, pct]) => ({ key, label: FAN_LABEL[key] ?? key, pct }))
}

/** "hardened_steel" -> "Hardened steel" */
export function humanize(value: string | null | undefined): string {
  if (!value) return '—'
  const s = value.replaceAll('_', ' ')
  return s[0].toUpperCase() + s.slice(1)
}

/** How serious a printer / plate event is (the same rules as the gateway's /api/v1/printers/log). */
export function severityOf(kind: string, data: Record<string, unknown>): Severity {
  if (kind === 'printer.failure.detected') {
    return data.severity === 'critical' || data.severity === 'info' ? data.severity : 'warning'
  }
  if (kind === 'printer.pause.unconfirmed') return 'critical'
  if ((kind === 'printer.print.finished' || kind === 'fleet.plate.finished') && data.succeeded === false) return 'warning'
  if (kind === 'printer.connection.changed' && data.online === false) return 'warning'
  if (kind === 'printer.calibration.finished' && data.succeeded === false && data.started !== false) return 'warning'
  return 'info'
}

export const SEVERITY_RANK: Record<Severity, number> = { info: 0, warning: 1, critical: 2 }

/** "/agents/telemetry" -> "Telemetry" */
export function sourceLabel(source: string | null | undefined): string {
  const name = (source ?? '').split('/').filter(Boolean).pop() ?? ''
  return { telemetry: 'Telemetry', fleet: 'Fleet Manager', gateway: 'Dashboard', coordinator: 'Coordinator' }[name] ?? (name || '—')
}
