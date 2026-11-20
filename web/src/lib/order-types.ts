// Shapes of the Order Pipeline API (gateway/app/orders.py).

export type OrderState =
  | 'LEAD'
  | 'RECEIVED'
  | 'PERSONALIZING'
  | 'SCHEDULING'
  | 'PRINTING'
  | 'QC'
  | 'SHIPPED'
  | 'NEEDS_ATTENTION'
  | 'CANCELLED'

export interface OrderItem {
  item_id: string
  product: string
  label: string
  text: string
  material: string
  color: string
  quantity: number
  niche?: string // the niche the line was sold under
  model: { status?: 'pending' | 'generating' | 'ready' | 'failed'; error?: string | null }
}

export interface OrderCard {
  order_id: string
  customer_name: string
  customer_ref: string
  channel: string
  due_by: string | null
  created_at: string
  state: OrderState
  stage: Exclude<OrderState, 'NEEDS_ATTENTION'>
  stage_since: string
  stuck: boolean
  status: string
  total_agorot: number
  paid_agorot: number
  units: number
  units_printed: number
  qc: 'pending' | 'passed' | null
  items: OrderItem[]
  plates: { plate_id: string; printer_id: string; status: string }[]
  /** W2-B: the live parcel (Shipped's sub-state). */
  shipment?: { shipment_id: string; carrier: string; tracking: string; tracking_url: string; status: ShipmentStatus; status_at: string } | null
}

export type ShipmentStatus = 'label_created' | 'in_transit' | 'delivered' | 'returned' | 'cancelled'
export type DocStatus = 'pending' | 'issued' | 'failed' | 'manual' | 'skipped'

export interface ShipTo {
  name?: string
  street?: string
  city?: string
  zip?: string
  phone?: string
}

export interface Shipment {
  shipment_id: string
  connector_id: string | null
  carrier: string
  service: string
  tracking: string
  tracking_url: string
  label_url: string
  status: ShipmentStatus
  ship_to: ShipTo
  events: { status: string; at: string; text: string; by: string }[]
  created_by: string
  created_at: string
  status_at: string
}

export interface OrderDocument {
  id: number
  payment_id: string
  kind: 'receipt' | 'invoice_receipt' | 'credit'
  status: DocStatus
  number: string | null
  url: string | null
  allocation: string | null
  error: string | null
  amount_agorot: number
  issued_at: string | null
}

export interface OrderDetail extends OrderCard {
  notes: string
  created_by: string
  pricing: {
    subtotal_agorot: number
    discount_agorot: number
    shipping_agorot: number
    total_agorot: number
    lines: { item_id: string; unit_price_agorot: number; line_total_agorot: number }[]
  }
  unit_status: { unit: string; status: string; attempt: number; tries?: number; item_text: string }[]
  attention: { from: string; reason: string } | null
  shipped: { carrier: string; tracking: string; by: string } | null
  cancelled: { reason: string; by: string } | null
  history: { from_state: string | null; to_state: string; message_type: string; at: string }[]
  payments: { payment_id: string; kind: 'payment' | 'refund'; amount_agorot: number; method: string; at: string; recorded_by: string }[]
  qc_checks: { passed: boolean; units_failed: number; units_checked: number; reason: string; checked_by: string; checked_at: string; checklist?: { item: string; ok: boolean }[] }[]
  /** The checklists of the niches its lines were sold under (each check once). */
  qc_checklist?: { item: string; niche: string; niche_name: string }[]
  jobs: { job_id: string; status: string; plate_id: string | null; printer_id: string | null; plate_status: string | null }[]
  documents?: OrderDocument[]
  shipments?: Shipment[]
  ship_to?: ShipTo
  invoicing_on?: boolean
}

export interface OrderStats {
  qc_failed_units_7d: number
  qc_checked_units_7d: number
  lead_to_ship_days: number | null
  lead_to_ship_days_prev: number | null
}

export interface PlateRow {
  plate_id: string
  printer_id: string
  material: string
  color: string
  status: 'scheduled' | 'printing'
  est_minutes: number
  created_at: string
  started_at: string | null
  jobs: { job_id: string; order_id: string }[]
}
