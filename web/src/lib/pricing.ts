// Unit economics and quotes (Wave 1 / M5): shapes of gateway/app/pricing.py. The rules (cost parts, failure
// allowance, channel fee, the price for a target margin ending in 9) are in src/printfarm/agents/finance/costing.py.

export const COST_PARTS = ['filament', 'electricity', 'failures', 'labour', 'parts', 'channel_fee', 'shipping'] as const
export type CostPart = (typeof COST_PARTS)[number]

export const COST_LABEL: Record<CostPart, string> = {
  filament: 'Filament',
  electricity: 'Electricity',
  failures: 'Failed prints',
  labour: 'Labour',
  parts: 'Parts & packaging',
  channel_fee: 'Channel fee',
  shipping: 'Shipping',
}

/** One colour token per cost part (stacked bars). */
export const COST_COLOR: Record<CostPart, string> = {
  filament: 'var(--niche-1)',
  electricity: 'var(--niche-3)',
  failures: 'var(--niche-7)',
  labour: 'var(--niche-5)',
  parts: 'var(--niche-6)',
  channel_fee: 'var(--niche-4)',
  shipping: 'var(--niche-8)',
}

export interface Economics {
  costs: Partial<Record<CostPart, number>>
  unit_cost: number
  gross_price: number
  net_price: number
  margin: number
  margin_pct: number | null
  profit_per_print_hour: number | null
  recommended_price: number | null
  target_margin_pct: number | null
  below_target: boolean
}

export interface UnitEconomicsRow extends Economics {
  sku: string
  name: string
  niche: string
  niche_name: string
  niche_color: number
  estimated: boolean
  grams: number
  print_minutes: number
  post_minutes: number
}

export interface UnitEconomicsReport {
  products: UnitEconomicsRow[]
  failure_rate: number
  failure_days: number
  channel: string | null
  channel_fee_pct: number
  labour_agorot_per_hour: number
  below_target: number
}

export interface Quote extends Economics {
  unit_price: number
  quantity: number
  extras_agorot: number
  shipping_agorot: number
  total_agorot: number
  total_margin_agorot: number
  print_hours: number
  grams: number
  print_minutes: number
  sku: string | null
  niche: string
  failure_rate: number
  title: string
  text: string
}

export interface QuoteRequest {
  title?: string
  sku?: string | null
  grams?: number | null
  print_minutes?: number | null
  material?: string
  color?: string
  labour_minutes?: number | null
  parts_agorot?: number | null
  quantity: number
  extras_agorot?: number
  shipping_agorot?: number
  channel?: string | null
  niche?: string | null
  target_margin_pct?: number | null
  unit_price_agorot?: number | null
  days?: number | null
}

/** Stacked-bar segments of a price: each cost part's share, then the margin (what is left of the net price). */
export function segments(e: Economics): { part: CostPart | 'margin'; agorot: number; pct: number }[] {
  const total = Math.max(e.net_price, e.unit_cost, 1)
  const out: { part: CostPart | 'margin'; agorot: number; pct: number }[] = COST_PARTS.filter((p) => (e.costs[p] ?? 0) > 0).map(
    (p) => ({ part: p, agorot: e.costs[p] ?? 0, pct: ((e.costs[p] ?? 0) * 100) / total }),
  )
  if (e.margin > 0) out.push({ part: 'margin', agorot: e.margin, pct: (e.margin * 100) / total })
  return out
}

/** Where the quote handed to the new-order dialog waits (this tab only). */
export const QUOTE_KEY = 'pf.quote.order'

export interface QuoteForOrder {
  sku: string
  quantity: number
  unit_price_agorot: number
  shipping_agorot: number
  customer_name?: string
  notes?: string
}

export function keepQuoteForOrder(q: QuoteForOrder): void {
  try {
    sessionStorage.setItem(QUOTE_KEY, JSON.stringify(q))
  } catch {
    /* storage blocked: the order dialog opens empty */
  }
}

export function takeQuoteForOrder(): QuoteForOrder | null {
  try {
    const raw = sessionStorage.getItem(QUOTE_KEY)
    sessionStorage.removeItem(QUOTE_KEY)
    return raw ? (JSON.parse(raw) as QuoteForOrder) : null
  } catch {
    return null
  }
}
