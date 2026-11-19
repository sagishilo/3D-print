// Filament logic without UI: shapes of /api/v1/inventory, live grams on active spools while a plate prints, and
// small helpers for the Filament screen.
import type { Printer } from './types'

export interface Spool {
  spool_id: string
  material: string
  color: string
  brand: string
  initial_grams: number
  remaining_grams: number
  printer_id: string | null
  slot: string | null
  status: 'stock' | 'loaded' | 'empty'
  location: string | null
  tag: string | null
  weighed_at: string | null
  created_at: string
}

export interface Reservation {
  plate_id: string
  printer_id: string
  material: string
  color: string
  grams: number
  created_at: string
}

export interface MaterialRow {
  material: string
  color: string
  supplier: string
  mode: 'auto' | 'manual'
  min_stock_grams: number
  lead_time_days: number
  buffer_days: number
  grams_per_spool: number
  cost_per_spool_agorot: number
  auto_approve_limit_agorot: number
  on_hand_grams: number
  reserved_grams: number
  on_order_spools: number
  spools_loaded: number
  spools_stock: number
  daily_grams: number
  days_left: number | null
  days_to_trigger: number
  state: 'ok' | 'soon' | 'triggered'
  below_min: boolean
  suggested_spools: number
  suggested_cost_agorot: number
  needs_approval: boolean
}

export interface InventoryAlert {
  alert_id: string
  kind: 'low_stock' | 'spool_short' | 'spool_empty' | 'low_item'
  severity: 'info' | 'warning' | 'critical'
  material: string
  color: string
  spool_id: string | null
  printer_id: string | null
  plate_id: string | null
  needed_grams: number | null
  available_grams: number | null
  message: string
  opened_at: string
  resolved_at: string | null
  resolved_by: string | null
}

export interface PurchaseOrder {
  po_id: string
  material: string
  color: string
  spools: number
  est_cost_agorot: number
  status: 'pending_approval' | 'placed' | 'ordered' | 'received' | 'rejected'
  approval_id: string | null
  supplier: string | null
  total_agorot: number | null
  ordered_at: string | null
  received_at: string | null
  created_at: string
  item_id?: string | null // a part, packaging or consumable (Wave 1 / M4)
  quantity?: number | null
}

export interface UsageRow {
  day: string
  material: string
  color: string
  grams: number
}

/**
 * Grams the current print has used so far on each active spool: the plate's reservation × the printer's live
 * progress, taken from the printer's loaded spools of that filament lowest first (as the inventory agent books it
 * when the plate finishes). Nothing is written while printing; this is what the screen shows meanwhile.
 */
export function inPrintGrams(spools: Spool[], reservations: Reservation[], printers: Printer[]): Map<string, number> {
  const out = new Map<string, number>()
  for (const r of reservations) {
    const p = printers.find((x) => x.id === r.printer_id)
    const st = p?.state
    if (!st || st.plate_id !== r.plate_id || !['printing', 'paused'].includes(st.status)) continue
    let left = r.grams * Math.min(Math.max(st.progress, 0), 1)
    const loaded = spools
      .filter((s) => s.status === 'loaded' && s.printer_id === r.printer_id && s.material === r.material && s.color === r.color)
      .sort((a, b) => a.remaining_grams - b.remaining_grams)
    loaded.forEach((s, i) => {
      if (left <= 0) return
      const take = i === loaded.length - 1 ? left : Math.min(left, s.remaining_grams)
      out.set(s.spool_id, (out.get(s.spool_id) ?? 0) + take)
      left -= take
    })
  }
  return out
}

export function where(s: Spool): string {
  if (s.printer_id) return `${s.printer_id}${s.slot ? ` · ${s.slot}` : ''}`
  return s.location ? `Shelf ${s.location}` : 'Shelf'
}

/** Daily totals for the last ``days`` days (oldest first), zero-filled. */
export function dailyTotals(rows: UsageRow[], days: number, today = new Date()): { day: string; grams: number }[] {
  const byDay = new Map<string, number>()
  for (const r of rows) byDay.set(r.day, (byDay.get(r.day) ?? 0) + r.grams)
  const out = []
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i))
    const key = d.toISOString().slice(0, 10)
    out.push({ day: key, grams: Math.round(byDay.get(key) ?? 0) })
  }
  return out
}

export const ALERT_LABEL: Record<InventoryAlert['kind'], string> = {
  low_stock: 'Below minimum',
  spool_short: 'Spool too short',
  spool_empty: 'Spool empty',
  low_item: 'Part or packaging low',
}
