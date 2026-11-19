// Stock beyond filament (Wave 1 / M4): finished pieces on the shelf, parts / packaging / consumables with the bill of
// materials of each SKU, and filament condition (drying, AMS humidity). Shapes of gateway/app/inventory.py.

export interface FinishedGood {
  sku: string
  name: string
  niche: string
  active: boolean
  on_hand: number
  reserved: number // promised to open orders
  free: number
  min_stock: number
  location: string
  in_production: number // pieces in open stock batches
  unit_cost_agorot: number | null // stock-batch filament + power per piece built
  value_agorot: number | null
  low: boolean
  restock: number // pieces a restock batch would queue
  updated_at: string | null
}

export type ItemKind = 'part' | 'packaging' | 'consumable'

export interface StockItem {
  item_id: string
  name: string
  kind: ItemKind
  unit: string
  on_hand: number
  min_qty: number
  reorder_qty: number
  supplier: string
  lead_days: number
  cost_agorot: number
  archived: boolean
  on_order: number
  used_by: { sku: string; qty: number }[]
  used_30d: number
  low: boolean
  suggested_qty: number
  suggested_cost_agorot: number
}

export interface BomRow {
  item_id: string
  qty: number
  name?: string
  kind?: ItemKind
  unit?: string
  cost_agorot?: number
}

export interface ConditionSpool {
  spool_id: string
  material: string
  color: string
  printer_id: string | null
  slot: string | null
  location: string | null
  opened_at: string | null
  dried_at: string | null
  days_open: number | null
  dry_after_days: number
  needs_drying: boolean
}

export interface Condition {
  spools: ConditionSpool[]
  due: number
  humid: { printer_id: string; unit: number; level: number }[]
  humid_level: number
  dry_after_days: Record<string, number>
}

export const KIND_LABEL: Record<ItemKind, string> = { part: 'Part', packaging: 'Packaging', consumable: 'Consumable' }

/** "Gift box S" -> "gift-box-s" (an item's id). */
export const itemIdFrom = (name: string): string =>
  name
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)

/** Bill-of-materials cost of one piece (agorot). */
export const bomCost = (rows: BomRow[]): number => Math.round(rows.reduce((a, r) => a + r.qty * (r.cost_agorot ?? 0), 0))

/** "12.5" -> 12.5; anything else -> NaN. */
export const qty = (s: string | number): number => (typeof s === 'number' ? s : s.trim() === '' ? NaN : Number(s))
