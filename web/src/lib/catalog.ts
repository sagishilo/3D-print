// Instant Catalog logic without UI: types of the gateway's catalog API (gateway/app/catalog.py), the batch the
// operator is building, and formatters for cards and the batch bar.

export interface CatalogProduct {
  sku: string
  name: string
  category: string
  material: string
  color: string
  price_agorot: number
  file_name: string
  sliced: boolean
  printer_model: string | null
  footprint_mm: [number, number]
  est_grams: number
  est_minutes: number
  estimated: boolean // grams and minutes typed in, not from a slicer
  active: boolean
  sold_7d: number
  sold_prev_7d: number
  in_production: number
  trend_pct: number | null
  created_at: string
  updated_at: string
  updated_by: string
  niche: string // the main niche (lib/niches.ts)
  tags: string[] // extra niches
  post_minutes?: number // finishing labour per piece (Wave 1 / M5)
  on_shelf?: number // finished pieces free on the shelf (Wave 1 / M4)
}

export interface ThreeMfInfo {
  sliced: boolean
  plates: number
  objects: number
  footprint_mm: [number, number] | null
  est_grams: number | null
  est_minutes: number | null
  printer_model: string | null
  filament: string | null
  warnings: string[]
}

export interface Upload {
  upload: string // SHA-256, the name in the uploads object store
  name: string
  bytes: number
  info: ThreeMfInfo
}

export interface MaterialNeed {
  material: string
  color: string
  need_grams: number
  available_grams: number
  short_grams: number
}

export interface BatchEstimate {
  skus: number
  pieces: number
  plates: number
  minutes: number
  grams: number
  bed_mm: [number, number]
  too_big: string[]
  materials: MaterialNeed[]
  unsliced: string[]
}

export const SORTS = [
  { key: 'demand', label: '7-day demand' },
  { key: 'newest', label: 'Newest' },
  { key: 'name', label: 'Name' },
  { key: 'price', label: 'Price' },
] as const
export type Sort = (typeof SORTS)[number]['key']

export const CATEGORY_LABEL: Record<string, string> = {
  stands: 'Card stands',
  displays: 'Slab displays',
  storage: 'Storage',
  home: 'Home',
  gifts: 'Gifts',
  kids: 'Kids',
  seasonal: 'Seasonal',
  other: 'Other',
}

/** Print time from minutes: "3 h 10 m", "42 m". */
export function minutes(m: number | null | undefined): string {
  if (m == null || !Number.isFinite(m)) return '—'
  const total = Math.max(0, Math.round(m))
  const h = Math.floor(total / 60)
  const rest = total % 60
  return h ? `${h} h ${String(rest).padStart(2, '0')} m` : `${rest} m`
}

/** "+64%", "−6%", "new" (sold this week, nothing the week before), "" (no sales at all). */
export function trendLabel(p: Pick<CatalogProduct, 'trend_pct' | 'sold_7d'>): string {
  if (p.trend_pct === null) return p.sold_7d > 0 ? 'new' : ''
  if (p.trend_pct === 0) return '±0%'
  return `${p.trend_pct > 0 ? '+' : '−'}${Math.abs(Math.round(p.trend_pct))}%`
}

export type Batch = Record<string, number> // sku -> pieces

export function batchItems(batch: Batch): string[] {
  return Object.entries(batch)
    .filter(([, n]) => n > 0)
    .map(([sku, n]) => `${sku}:${n}`)
}

export function estimateUrl(batch: Batch): string {
  return `/api/v1/catalog/estimate?${batchItems(batch).map((i) => `items=${encodeURIComponent(i)}`).join('&')}`
}

/** "STD-SLAB-5T × 4 · STD-SLAB-1E × 8" */
export function batchLine(batch: Batch): string {
  return Object.entries(batch)
    .filter(([, n]) => n > 0)
    .map(([sku, n]) => `${sku} × ${n}`)
    .join(' · ')
}

/** "3 plates · 9 h 40 m · 640 g" */
export function estimateLine(e: BatchEstimate): string {
  return `${e.plates} plate${e.plates === 1 ? '' : 's'} · ${minutes(e.minutes)} · ${Math.round(e.grams)} g`
}

/** "PLA black short by 330 g" for every filament the batch would run out of. */
export function shortages(e: BatchEstimate): string[] {
  return e.materials.filter((m) => m.short_grams > 0).map((m) => `${m.material} ${m.color} short by ${Math.round(m.short_grams)} g`)
}

/** SKU rules of contracts/catalog.py, for the form. */
export function skuError(sku: string): string | null {
  if (!sku) return 'the SKU is missing'
  return /^[A-Z0-9][A-Z0-9-]{1,31}$/.test(sku) ? null : 'a SKU is 2-32 capitals, digits and dashes (e.g. STD-SLAB-1E)'
}

/** A file name to a SKU suggestion: "slab easel v2.3mf" -> "SLAB-EASEL-V2". */
export function skuFromName(name: string): string {
  return name
    .replace(/\.(gcode\.)?3mf$/i, '')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
}
