// Plain-language lines for the live feed, one per event type (contracts in src/printfarm/contracts/messages.py).
import { bytes, duration, shekels } from './format'
import type { Envelope, Tone } from './types'

export type Category = 'printer' | 'order' | 'stock' | 'money' | 'social' | 'system'

export interface Described {
  kind: string // e.g. "printer.pause.confirmed"
  text: string
  tone: Tone
  category: Category
  printerId?: string
  orderId?: string
  safety?: boolean // a safety alert: shown as a persistent warning
}

const STAGE_LABEL: Record<string, string> = {
  LEAD: 'Lead',
  RECEIVED: 'Received',
  PERSONALIZING: 'Personalization',
  SCHEDULING: 'Scheduling',
  PRINTING: 'Printing',
  QC: 'QC',
  SHIPPED: 'Shipped',
  NEEDS_ATTENTION: 'Needs attention',
  CANCELLED: 'Cancelled',
}
export const stageLabel = (s: unknown): string => (s ? (STAGE_LABEL[String(s)] ?? String(s)) : 'new')

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
    case 'order.order.created':
      return out(`Order ${d.order_id} from ${d.customer_name} · ${d.units} unit(s), ${shekels(d.total_agorot)} (${d.channel})`, 'accent', 'order')
    case 'order.stage.changed':
      return out(
        `Order ${d.order_id}: ${stageLabel(d.from_state)} → ${stageLabel(d.to_state)} (${d.by})${d.reason ? ` · ${d.reason}` : ''}`,
        d.to_state === 'NEEDS_ATTENTION' ? 'warn' : d.to_state === 'CANCELLED' ? 'idle' : 'accent',
        'order',
      )
    case 'order.order.updated': {
      const what: Record<string, string> = {
        'personalization.model.ready': 'a model is ready',
        'personalization.model.failed': 'a model failed',
        'fleet.job.started': 'a unit started printing',
        'fleet.job.completed': 'a unit is printed',
        'fleet.job.failed': 'a unit failed and is queued again',
        'order.qc.recorded': 'quality check recorded',
      }
      return out(`Order ${d.order_id}: ${what[d.cause] ?? String(d.cause).replaceAll('.', ' ')}`, 'idle', 'order')
    }
    case 'order.move.rejected':
      return out(`Order ${d.order_id}: not moved to ${stageLabel(d.to_state)} · ${d.reason}`, 'warn', 'order')
    case 'order.qc.recorded':
      return out(
        `Order ${d.order_id} ${d.passed ? 'passed QC' : `failed QC (${d.failed_units?.length ?? 0} unit(s) reprinted)`}` +
          `${d.reason ? ` · ${d.reason}` : ''} (${d.by})`,
        d.passed ? 'ok' : 'warn',
        'order',
      )
    case 'order.order.shipped':
      return out(`Order ${d.order_id} shipped${d.carrier ? ` with ${d.carrier}` : ''} · ${shekels(d.total_agorot)}`, 'ok', 'order')
    case 'order.order.cancelled':
      return out(`Order ${d.order_id} cancelled${d.reason ? `: ${d.reason}` : ''} (${d.by})`, 'idle', 'order')
    case 'order.order.needs_attention':
      return out(`Order ${d.order_id} needs attention: ${d.reason}`, 'warn', 'order')
    case 'order.payment.recorded':
      return out(`Payment ${shekels(d.amount_agorot)} (${d.method}) for ${d.order_id ?? 'no order'}`, 'ok', 'money')
    case 'order.payment.refunded':
      return out(`Refund ${shekels(d.amount_agorot)} for ${d.order_id ?? 'no order'}`, 'warn', 'money')
    case 'social.capture.scored':
      if (d.status === 'lead') return out(`Lead in ${d.group_name || 'a pasted post'} · intent ${Number(d.intent_score).toFixed(2)} · ${d.summary}`, 'ok', 'social')
      if (d.status === 'waiting' || d.status === 'failed')
        return out(`Capture ${d.status}${d.group_name ? ` (${d.group_name})` : ''}: ${d.reason}`, d.status === 'failed' ? 'danger' : 'warn', 'social')
      if (d.status === 'duplicate') return out(`Captured again: ${d.reason}`, 'idle', 'social')
      return out(`Capture ${d.status === 'ignored' ? 'ignored' : 'scored'}${d.group_name ? ` (${d.group_name})` : ''}${d.reason ? `: ${d.reason}` : d.summary ? `: ${d.summary}` : ''}`, 'idle', 'social')
    case 'social.lead.detected':
      return out(`New lead · ${d.summary}`, 'ok', 'social')
    case 'social.reply.drafted':
      return d.failed ? out(`Lead ${d.lead_id}: no draft (${d.failed}); write the reply yourself`, 'warn', 'social')
        : out(`Reply drafted for ${d.lead_id}: waiting for your review`, 'accent', 'social')
    case 'social.lead.changed':
      return out(`Lead ${d.lead_id} ${d.status === 'approved' ? 'approved' : d.status === 'posted' ? 'posted' : 'rejected'} (${d.by})`,
        d.status === 'dismissed' ? 'idle' : 'ok', 'social')
    case 'social.capture.labelled':
      return out(`Capture labelled ${d.label === 'buyer' ? 'buyer' : 'not a buyer'} (${d.by})`, 'idle', 'social')
    case 'catalog.product.updated':
      return out(`${d.sku} “${d.name}” saved${d.sliced ? ' · sliced' : ' · not sliced'}${d.active ? '' : ' · switched off'} (${d.by})`, 'ok', 'stock')
    case 'catalog.product.rejected':
      return out(`${d.sku}: ${d.action === 'slice' ? 'not sliced' : 'not saved'} · ${d.reason}`, 'warn', 'stock')
    case 'catalog.niche.updated':
      return out(`Niche “${d.name}” ${d.archived ? 'archived' : 'saved'}${d.skus_moved ? ` · ${d.skus_moved} product${d.skus_moved === 1 ? '' : 's'} moved` : ''} (${d.by})`, 'ok', 'stock')
    case 'order.stock.moved': {
      const what: Record<string, string> = { built: 'on the shelf', reserved: 'reserved from the shelf for', released: 'back on sale from',
        sold: 'sold from the shelf with', counted: 'counted on the shelf' }
      const n = Math.abs(Number(d.delta_on_hand) || Number(d.delta_reserved) || 0)
      return out(`${d.sku}: ${n ? `${n} ` : ''}${what[String(d.reason)] ?? String(d.reason)}${d.order_id && d.reason !== 'counted' && d.reason !== 'built' ? ` ${d.order_id}` : ''}`, 'idle', 'stock')
    }
    case 'order.stock.rule_changed':
      return out(`${d.sku}: keep at least ${d.min_stock} on the shelf${d.location ? ` (${d.location})` : ''} (${d.by})`, 'idle', 'stock')
    case 'inventory.item.changed':
      return out(`${d.name}: ${d.reason === 'saved' ? 'saved' : `${Number(d.delta) > 0 ? '+' : ''}${d.delta} ${d.reason}`} · ${d.on_hand} on hand`, 'idle', 'stock')
    case 'inventory.bom.changed':
      return out(`${d.sku}: parts per piece saved (${(d.lines as unknown[]).length} item${(d.lines as unknown[]).length === 1 ? '' : 's'}, ${d.by})`, 'idle', 'stock')
    case 'inventory.spool.dried':
      return out(`Spool ${d.spool_id} (${d.material} ${d.color}) dried (${d.by})`, 'ok', 'stock')
    case 'integrations.sync.finished':
      return d.ok
        ? out(`${d.connector_id} synced · ${d.created} new, ${d.updated} changed`, 'idle', 'system')
        : out(`${d.connector_id} sync failed (${d.failures} in a row): ${d.error}`, d.failures >= 3 ? 'danger' : 'warn', 'system')
    case 'integrations.connector.changed':
      return out(`${d.connector_id} ${d.enabled ? 'on' : 'off'} · ${d.mode === 'live' ? 'live service' : 'test data'} (${d.by})`, 'idle', 'system')
    case 'notify.queued':
      return out(`Notification: ${d.title}`, d.severity === 'critical' ? 'danger' : d.severity === 'warning' ? 'warn' : 'idle', 'system')
    case 'catalog.niche.rejected':
      return out(`Niche ${d.slug} not changed · ${d.reason}`, 'warn', 'stock')
    case 'personalization.model.ready':
      return out(`Model for ${d.order_id} ready · ~${round(d.est_grams)} g, ~${round(d.est_minutes)} min${d.sliced ? '' : ' (estimate)'}`, 'ok', 'order')
    case 'personalization.model.failed':
      return out(`Model for ${d.order_id} failed: ${d.reason}`, 'danger', 'order')
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
    case 'inventory.spool.registered':
      return out(`Spool ${d.spool_id} registered · ${d.brand ? `${d.brand} ` : ''}${d.material} ${d.color}, ${round(d.remaining_grams)} g`, 'idle', 'stock')
    case 'inventory.spool.adjusted':
      return out(`Spool ${d.spool_id} weighed: ${round(d.remaining_grams)} g (${d.delta_grams >= 0 ? '+' : ''}${round(d.delta_grams)} g, ${d.by})`, 'idle', 'stock')
    case 'inventory.rule.changed':
      return out(`Reorder rule ${d.material} ${d.color}: min ${round(d.min_stock_grams)} g, ${d.mode}, ${d.supplier || 'no supplier'} (${d.by})`, 'accent', 'stock')
    case 'inventory.alert.raised':
      return out(d.message, d.severity === 'critical' ? 'danger' : d.severity === 'warning' ? 'warn' : 'idle', 'stock', {
        printerId: d.printer_id ?? undefined,
      })
    case 'inventory.alert.resolved':
      return out(`Filament alert closed (${String(d.alert_kind).replaceAll('_', ' ')}, ${d.by})`, 'ok', 'stock')
    case 'finance.settings.changed':
      return out(`Finance settings changed by ${d.by}: ${d.business_type}, VAT ${Math.round(d.vat_rate * 100)}%${d.confirmed ? ' (confirmed)' : ''}`, 'accent', 'money')
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
