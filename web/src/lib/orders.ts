// Order Pipeline logic without UI: board columns, which drops are allowed (mirrors the coordinator's saga, which has
// the final word), pricing preview (mirrors contracts/orders.py) and small formatters.
import type { OrderCard } from './order-types'

export type Stage = 'LEAD' | 'RECEIVED' | 'PERSONALIZING' | 'SCHEDULING' | 'PRINTING' | 'QC' | 'SHIPPED'

export interface Column {
  stage: Stage
  label: string
  hint: string // what a drop here does (design: "Drop dispatches job", "Printer events only", ...)
}

export const COLUMNS: Column[] = [
  { stage: 'LEAD', label: 'Lead', hint: 'Drop: back to lead is not possible' },
  { stage: 'RECEIVED', label: 'Order received', hint: 'Drop: confirms a lead' },
  { stage: 'PERSONALIZING', label: 'Personalization', hint: 'Drop prepares the models' },
  { stage: 'SCHEDULING', label: 'Slicing / scheduling', hint: 'Drop dispatches print jobs' },
  { stage: 'PRINTING', label: 'Printing', hint: 'Printer events only' },
  { stage: 'QC', label: 'Quality control', hint: 'Manual pass / fail' },
  { stage: 'SHIPPED', label: 'Shipped', hint: 'Drop: ship a passed order' },
]

export const CHANNELS = ['manual', 'whatsapp', 'facebook', 'instagram', 'etsy', 'market', 'website', 'api', 'stock'] as const
export const PAYMENT_METHODS = ['bit', 'paybox', 'bank_transfer', 'cash', 'card'] as const

export interface Product {
  key: string
  label: string
  defaultPrice: number // agorot
  needsText: boolean
}
export const PRODUCTS: Product[] = [
  { key: 'nameplate', label: 'Nameplate', defaultPrice: 4500, needsText: true },
  { key: 'catalog', label: 'Catalog item', defaultPrice: 0, needsText: false }, // the price comes from the catalog
]

export type DropAction =
  | { ok: true; kind: 'advance'; to: Stage }
  | { ok: true; kind: 'ship' }
  | { ok: true; kind: 'retry_texts' } // a failed nameplate: open the order to fix its text
  | { ok: false; reason: string }

/** What dropping ``card`` on ``to`` would do. The saga re-checks and answers with evt.order.move.rejected if not. */
export function dropAction(card: OrderCard, to: Stage): DropAction {
  const from = card.state
  if (to === card.stage && from !== 'NEEDS_ATTENTION') return { ok: false, reason: 'already there' }
  if (to === 'PRINTING') return { ok: false, reason: 'printing starts when a printer takes the plate' }
  if (to === 'QC') return { ok: false, reason: 'an order reaches QC when all its units are printed' }
  if (to === 'LEAD') return { ok: false, reason: 'an order cannot go back to lead' }
  if (to === 'RECEIVED') return from === 'LEAD' ? { ok: true, kind: 'advance', to } : { ok: false, reason: 'only a lead can be confirmed' }
  if (to === 'PERSONALIZING') {
    if (from === 'RECEIVED') return { ok: true, kind: 'advance', to }
    if (from === 'NEEDS_ATTENTION' && card.stage === 'PERSONALIZING') return { ok: true, kind: 'retry_texts' }
    return { ok: false, reason: 'only received orders start personalization' }
  }
  if (to === 'SCHEDULING') {
    if (from === 'PERSONALIZING') {
      const waiting = card.items.filter((i) => i.model.status !== 'ready').map((i) => i.item_id)
      return waiting.length ? { ok: false, reason: `model of item ${waiting.join(', ')} is not ready yet` } : { ok: true, kind: 'advance', to }
    }
    if (from === 'NEEDS_ATTENTION' && ['PRINTING', 'SCHEDULING', 'QC'].includes(card.stage)) return { ok: true, kind: 'advance', to }
    return { ok: false, reason: 'the models must be ready first' }
  }
  if (to === 'SHIPPED') return from === 'QC' && card.qc === 'passed' ? { ok: true, kind: 'ship' } : { ok: false, reason: 'pass QC first' }
  return { ok: false, reason: 'not allowed' }
}

export interface Line {
  product: string
  sku?: string // catalog lines
  text: string
  material: string
  color: string
  quantity: number
  unit_price_agorot: number | null
}

export function unitPrice(line: Line): number {
  return line.unit_price_agorot ?? PRODUCTS.find((p) => p.key === line.product)?.defaultPrice ?? 0
}

export interface Pricing {
  lines: number[]
  subtotal: number
  discount: number
  shipping: number
  total: number
  units: number
  error: string | null
}

export function priceOrder(lines: Line[], discount = 0, shipping = 0): Pricing {
  const totals = lines.map((l) => unitPrice(l) * l.quantity)
  const subtotal = totals.reduce((a, b) => a + b, 0)
  let error: string | null = null
  if (!lines.length) error = 'an order needs at least one item'
  else if (lines.some((l) => PRODUCTS.find((p) => p.key === l.product)?.needsText && !l.text.trim())) error = 'every nameplate needs its text'
  else if (lines.some((l) => l.product === 'catalog' && !l.sku)) error = 'pick the SKU of every catalog item'
  else if (lines.some((l) => l.text.length > 40)) error = 'a text can be at most 40 characters'
  else if (lines.some((l) => !(l.quantity >= 1 && l.quantity <= 50))) error = 'quantity must be 1-50'
  else if (discount < 0 || shipping < 0) error = 'discount and shipping cannot be negative'
  else if (discount > subtotal) error = 'the discount is larger than the items’ total'
  return { lines: totals, subtotal, discount, shipping, total: subtotal - discount + shipping, units: lines.reduce((a, l) => a + l.quantity, 0), error }
}

/** "Nameplate ×2 + 1 more" */
export function itemsSummary(card: OrderCard): string {
  const [first, ...rest] = card.items
  if (!first) return '—'
  return `${first.label} ×${first.quantity}${rest.length ? ` + ${rest.length} more` : ''}`
}

/** shekels input ("12.50") -> agorot */
export function toAgorot(shekels: string | number): number {
  return Math.round(Number(shekels) * 100)
}

export type DueFilter = 'all' | 'today' | 'week' | 'overdue'

export function dueMatches(due: string | null, filter: DueFilter, now = new Date()): boolean {
  if (filter === 'all') return true
  if (!due) return false
  const d = new Date(due)
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const endOfDay = new Date(startOfDay.getTime() + 86_400_000)
  if (filter === 'overdue') return d < now
  if (filter === 'today') return d >= startOfDay && d < endOfDay
  return d >= startOfDay && d < new Date(startOfDay.getTime() + 7 * 86_400_000)
}
