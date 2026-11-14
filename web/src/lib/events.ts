// Plain-language lines for the live feed, one per event type (contracts in src/printfarm/contracts/messages.py).
import { bytes, duration, shekels } from './format'
import type { Envelope, Tone } from './types'

export type Category = 'printer' | 'order' | 'stock' | 'money' | 'system'

export interface Described {
  kind: string // e.g. "printer.pause.confirmed"
  text: string
  tone: Tone
  category: Category
  printerId?: string
  orderId?: string
  safety?: boolean // a safety alert: shown as a persistent warning
}

/** "io.printfarm.printer.pause.confirmed.v1" -> "printer.pause.confirmed" */
export function kindOf(type: string): string {
  return type.replace(/^io\.printfarm\./, '').replace(/\.v\d+$/, '')
}

const round = (n: unknown) => (typeof n === 'number' ? Math.round(n) : n)

export function describe(env: Envelope): Described {
  const kind = kindOf(env.type ?? '')
  const d = (env.data ?? {}) as Record<string, any>
  const base = { kind, printerId: d.printer_id as string | undefined, orderId: d.order_id as string | undefined }
  const out = (text: string, tone: Tone, category: Category, extra: Partial<Described> = {}): Described => ({
    ...base,
    text,
    tone,
    category,
    ...extra,
  })

  switch (kind) {
    case 'order.order.received':
      return out(`Order ${d.order_id} received · ${d.quantity}× ${d.product} “${d.text}” (${d.color})`, 'accent', 'order')
    case 'order.order.completed':
      return out(`Order ${d.order_id} completed`, 'ok', 'order')
    case 'order.order.needs_attention':
      return out(`Order ${d.order_id} needs attention: ${d.reason}`, 'warn', 'order')
    case 'order.payment.recorded':
      return out(`Payment ${shekels(d.amount_agorot)} (${d.method}) for ${d.order_id ?? 'no order'}`, 'ok', 'money')
    case 'order.payment.refunded':
      return out(`Refund ${shekels(d.amount_agorot)} for ${d.order_id ?? 'no order'}`, 'warn', 'money')
    case 'personalization.model.ready':
      return out(`Nameplate for ${d.order_id} ready · ~${round(d.est_grams)} g, ~${round(d.est_minutes)} min${d.sliced ? '' : ' (estimate)'}`, 'ok', 'order')
    case 'personalization.model.failed':
      return out(`Nameplate for ${d.order_id} failed: ${d.reason}`, 'danger', 'order')
    case 'fleet.job.queued':
      return out(`Job ${d.job_id} queued`, 'idle', 'order')
    case 'fleet.job.started':
      return out(`Job ${d.job_id} printing on ${d.printer_id}`, 'accent', 'printer')
    case 'fleet.job.completed':
      return out(`Job ${d.job_id} done`, 'ok', 'order')
    case 'fleet.job.failed':
      return out(`Job ${d.job_id} failed: ${d.reason}`, 'danger', 'order')
    case 'fleet.plate.scheduled':
      return out(`Plate ${d.plate_id} → ${d.printer_id} · ${d.job_ids?.length ?? 0} part(s), ~${duration((d.est_minutes ?? 0) * 60)}`, 'accent', 'printer')
    case 'fleet.plate.finished':
      return out(
        `Plate ${d.plate_id} on ${d.printer_id} ${d.succeeded ? 'finished' : 'failed'} · ${round(d.grams_used)} g`,
        d.succeeded ? 'ok' : 'danger',
        'printer',
      )
    case 'printer.plate.uploaded':
      return out(`${d.remote_file} uploaded to ${d.printer_id} (${bytes(d.bytes)}, ${d.seconds} s)`, 'idle', 'printer')
    case 'printer.print.started':
      return out(`${d.printer_id} started ${d.plate_id}`, 'accent', 'printer')
    case 'printer.print.finished':
      return out(
        `${d.printer_id} ${d.succeeded ? 'finished' : 'stopped'} ${d.plate_id}${d.reason ? ` · ${d.reason}` : ''}`,
        d.succeeded ? 'ok' : 'danger',
        'printer',
      )
    case 'printer.failure.detected':
      return out(
        `${d.printer_id}: ${String(d.detector).replaceAll('_', ' ')} — ${d.message} (${d.action_taken})`,
        d.severity === 'critical' ? 'danger' : d.severity === 'warning' ? 'warn' : 'idle',
        'printer',
      )
    case 'printer.pause.confirmed':
      return out(`${d.printer_id} paused in ${((d.latency_ms ?? 0) / 1000).toFixed(1)} s · ${d.reason} (${d.requested_by})`, 'ok', 'printer')
    case 'printer.pause.unconfirmed':
      return out(
        `${d.printer_id} did NOT pause after ${d.attempts} attempt(s): still ${d.last_status ?? 'unknown'}${d.online ? '' : ', offline'} · ${d.reason}`,
        'danger',
        'printer',
        { safety: true },
      )
    case 'printer.calibration.started':
      return out(`${d.printer_id}: ${String(d.routine).replaceAll('_', ' ')} started (${d.requested_by})`, 'accent', 'printer')
    case 'printer.calibration.finished':
      return out(
        `${d.printer_id}: ${String(d.routine).replaceAll('_', ' ')} ${d.succeeded ? 'done' : d.started === false ? 'not started' : 'failed'}` +
          (d.succeeded ? ` in ${duration(d.seconds)}` : ` · ${d.detail}`),
        d.succeeded ? 'ok' : d.started === false ? 'idle' : 'warn',
        'printer',
      )
    case 'printer.connection.changed':
      return out(`${d.printer_id} ${d.online ? 'connected' : 'disconnected'}`, d.online ? 'ok' : 'warn', 'printer')
    case 'approval.request.created':
      return out(`Approval needed: ${d.summary}`, 'warn', 'system')
    case 'approval.request.decided':
      return out(`${d.approved ? 'Approved' : 'Rejected'} by ${d.decided_by}: ${d.action}`, d.approved ? 'ok' : 'idle', 'system')
    case 'inventory.stock.low':
      return out(`Low stock: ${d.material} ${d.color} · ${round(d.on_hand_grams)} g left`, 'warn', 'stock')
    case 'inventory.reorder.placed':
      return out(`Reorder ${d.spools}× ${d.material} ${d.color} (~${shekels(d.est_cost_agorot)})`, 'accent', 'stock')
    case 'inventory.po.ordered':
      return out(`PO ${d.po_id} ordered from ${d.supplier} · ${shekels(d.total_agorot)}`, 'accent', 'stock')
    case 'inventory.po.received':
      return out(`PO ${d.po_id} received · ${d.spool_ids?.length ?? 0} spool(s)`, 'ok', 'stock')
    case 'inventory.filament.consumed':
      return out(`${round(d.grams)} g ${d.material} ${d.color} used · ${round(d.remaining_grams)} g left on ${d.spool_id}`, 'idle', 'stock')
    case 'inventory.spool.loaded':
      return out(`Spool ${d.spool_id} ${d.printer_id ? `loaded on ${d.printer_id}` : 'back on the shelf'}`, 'idle', 'stock')
    case 'finance.entry.created':
      return out(`Ledger: ${d.ledger} (${d.entry_kind}) ${shekels(d.gross_agorot)}`, 'idle', 'money')
    case 'system.config.changed':
      return out(`Setting ${d.key} → ${JSON.stringify(d.value)} by ${d.changed_by || 'unknown'}`, 'accent', 'system')
    case 'message.dead_lettered':
      return out(`Dead letter: ${d.original_subject} (${d.consumer}) — ${d.error}`, 'danger', 'system')
    default:
      return out(kind || 'event', 'idle', 'system')
  }
}
