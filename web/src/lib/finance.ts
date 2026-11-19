// Shapes of /api/v1/finance (gateway/app/finance.py) and small helpers for the Finance screen.

export type PeriodKind = 'month' | 'quarter' | 'year'

export interface PeriodInfo {
  kind: string
  start: string
  end: string
  label: string
}

export interface Pnl {
  revenue_agorot: number
  expenses_agorot: number
  profit_agorot: number
  margin_pct: number | null
  mix: Record<string, number>
  electricity_agorot: number
  electricity_estimated: boolean
  electricity_from_jobs: boolean
}

export interface Report {
  period: PeriodInfo
  previous: PeriodInfo
  current: Pnl
  before: Pnl
  change_pct: { revenue: number | null; expenses: number | null; profit: number | null }
  margin_change_pt: number | null
  print_hours: number
  receivables: { total_agorot: number; orders: number }
}

export interface TrendMonth {
  month: string
  label: string
  revenue_agorot: number
  expenses_agorot: number
  profit_agorot: number
}

export interface ProductRow {
  product_type: string
  units: number
  revenue_agorot: number
  costs: Record<string, number>
  margin_agorot: number
  margin_pct: number | null
  /** Spent on stock batches (made for the shelf): shown apart, not taken from this period's margin. */
  stock_build_agorot?: number
}

export interface Receivable {
  order_id: string
  customer_name: string
  total_agorot: number
  paid_agorot: number
  due_agorot: number
  shipped_at: string | null
}

export interface IncomeEntry {
  entry_date: string
  order_id: string | null
  customer_name: string | null
  method: string
  gross_agorot: number
  vat_agorot: number
  net_agorot: number
  receipt_no: string | null
  product_type: string | null
  source: 'auto' | 'manual'
}

export interface ExpenseEntry {
  entry_date: string
  category: string
  vendor: string | null
  kind: 'cash' | 'estimated' | 'allocated'
  gross_agorot: number
  vat_agorot: number
  net_agorot: number
  job_id: string | null
  product_type: string | null
  note: string | null
}

export interface VatPeriod {
  period: PeriodInfo
  output_vat_agorot: number
  input_vat_agorot: number
  payable_agorot: number
  turnover_gross_agorot: number
}

export interface VatReport {
  year: number
  business_type: 'murshe' | 'patur'
  periods: VatPeriod[]
  current: PeriodInfo
  turnover_gross_agorot: number
  turnover_ceiling_agorot: number | null
}

export interface FinanceSettings {
  business_type: 'murshe' | 'patur'
  vat_rate: number
  electricity_agorot_per_kwh: number
  default_printer_watts: number
  default_filament_agorot_per_gram: number
  turnover_ceiling_agorot: number | null
  confirmed: boolean
  confirmed_by: string | null
  confirmed_at: string | null
  updated_by: string | null
  updated_at: string
  placeholders: string[]
  filament_prices: { material: string; color: string; agorot_per_gram: number; po_id: string; at: string }[]
}

export const EXPENSE_CATEGORIES = ['filament', 'electricity', 'shipping', 'packaging', 'equipment', 'software', 'marketing', 'misc'] as const

export const CATEGORY_LABEL: Record<string, string> = {
  filament: 'Filament (spool purchases)',
  electricity: 'Electricity',
  shipping: 'Shipping',
  packaging: 'Packaging',
  equipment: 'Equipment',
  software: 'Software & hosting',
  marketing: 'Marketing',
  misc: 'Misc',
}

/** "▲ 9% vs Oct" / "▼ 3%" / "" */
export function changeLabel(pct: number | null, vs: string): string {
  if (pct == null) return `no data for ${vs}`
  return `${pct >= 0 ? '▲' : '▼'} ${Math.abs(pct)}% vs ${vs}`
}

/** Short month of a period label, for "vs Oct". */
export function shortLabel(p: PeriodInfo): string {
  return p.kind === 'month' ? p.label.split(' ')[0] : p.label
}

export function exportUrl(ledger: 'income' | 'expenses', period: PeriodKind, month: string): string {
  return `/api/v1/finance/export.csv?${new URLSearchParams({ ledger, period, month })}`
}

/** Days since an ISO time. */
export function daysSince(iso: string | null, now = Date.now()): number | null {
  return iso ? Math.floor((now - new Date(iso).getTime()) / 86_400_000) : null
}
