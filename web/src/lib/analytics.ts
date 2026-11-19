// Niche scorecard, Trend Analyst signals and report downloads (Wave 1 / M6): shapes of gateway/app/analytics.py.

export interface Suggestion {
  kind: 'batch' | 'price' | 'quality' | 'demand'
  text: string
  to: string
  sku?: string
  quantity?: number
  price_agorot?: number
}

export interface SignalRow {
  signal_id?: string
  scope: 'niche' | 'sku'
  subject: string
  niche: string
  direction: 'rising' | 'falling'
  strength: number
  change_pct: number | null
  evidence: string[]
  label?: string
}

export interface NicheScore {
  niche: string
  name: string
  color: number
  target_margin_pct: number | null
  revenue_agorot: number
  cost_agorot: number
  profit_agorot: number
  margin_pct: number | null
  revenue_prev_agorot: number
  profit_prev_agorot: number
  units: number
  units_prev: number
  demand_pct: number | null
  print_hours: number
  profit_per_print_hour_agorot: number | null
  qc_checked: number
  qc_failed: number
  qc_fail_pct: number | null
  leads: number
  leads_prev: number
  direction: 'rising' | 'falling' | null
  rising: string[]
  falling: string[]
  rising_detail: SignalRow[]
  below_target: number
  insight: string | null
  suggestions: Suggestion[]
}

export interface Scorecard {
  days: number
  end: string
  niches: NicheScore[]
  revenue_agorot: number
  signals_day: string | null
  rolled_at: string | null
}

export interface Signals {
  day: string | null
  signals: SignalRow[]
  insights: { niche: string; text: string; day: string }[]
}

export type Format = 'csv' | 'xlsx'

/** A report as a download (the gateway answers ?format=csv|xlsx with a file; the session cookie authorizes it). */
export function download(path: string, format: Format): string {
  return `${path}${path.includes('?') ? '&' : '?'}format=${format}`
}

/** "▲ 60%" / "▼ 20%" / "new" / "—" */
export function demandLabel(pct: number | null, units: number): string {
  if (pct == null) return units > 0 ? 'new' : '—'
  return `${pct >= 0 ? '▲' : '▼'} ${Math.abs(pct)}%`
}
