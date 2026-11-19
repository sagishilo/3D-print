// Today (Wave 1 / M7): what waits for a person across the farm, ranked. Shapes of gateway/app/today.py.
import type { Role } from '@/lib/roles'

export type Priority = 'now' | 'today' | 'week'

export type TodayAction =
  | { type: 'command'; name: string; body: Record<string, unknown>; label: string; confirm: string }
  | { type: 'link'; to: string; label: string }
  | { type: 'approvals'; label: string }

export interface TodayItem {
  id: string
  kind: string
  priority: Priority
  title: string
  why: string
  action: TodayAction
  min_role: Role
  visible_to: Role
  expert: boolean
  niche: string | null
  age_s: number
  count: number
  score: number
}

export interface TodayList {
  now: TodayItem[]
  today: TodayItem[]
  week: TodayItem[]
  count: number
  generated_at: string
}

export const GROUP_LABEL: Record<Priority, { title: string; hint: string }> = {
  now: { title: 'Now', hint: 'stops the farm until someone acts' },
  today: { title: 'Today', hint: 'customers and decisions waiting' },
  week: { title: 'This week', hint: 'stock, money and upkeep' },
}

/** An icon per kind of work (components/ui/Icon.vue names). */
export const KIND_ICON: Record<string, string> = {
  bed: 'check', attention: 'alert', confirm: 'inbox', qc: 'check', ship: 'external', stuck: 'history', leads: 'activity',
  approval: 'inbox', po: 'disc', receive: 'download', spool_short: 'alert', spool_empty: 'disc', low_stock: 'disc',
  low_item: 'grid', restock: 'grid', dry: 'disc', receivable: 'bars', margin: 'bars', backup: 'lock', dlq: 'alert',
  agents: 'gear',
}

/** What a person in this mode sees: technical items only in Expert mode. */
export function visible(list: TodayList | null, expert: boolean): Record<Priority, TodayItem[]> {
  const keep = (items: TodayItem[] = []) => items.filter((i) => expert || !i.expert)
  return { now: keep(list?.now), today: keep(list?.today), week: keep(list?.week) }
}

/** "3 h" / "2 d" (how long it has been waiting). */
export function waited(seconds: number): string {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`
  return `${Math.round(seconds / 86400)} d`
}

// ---------------------------------------------------------------- the summary above the tasks (KPIs, charts)

export interface TodaySummary {
  printers: { total: number; online: number; printing: number; utilization_pct: number }
  orders_today: number
  revenue_today_agorot: number | null // null: not for viewers
  alerts: { filament: number; parts: number; shelf: number }
  week: { day: string; orders: number; revenue_agorot: number | null }[]
  stages: { state: string; count: number }[]
  niches: { niche: string; name: string; color: number; revenue_agorot: number }[]
  money: boolean
}

/** What a task is about (the table's Category column) and which filter pill shows it. */
export type TaskFilter = 'all' | 'now' | 'production' | 'sales' | 'inventory'

export const CATEGORY: Record<string, { label: string; filter: Exclude<TaskFilter, 'all' | 'now'> | 'system' }> = {
  bed: { label: 'Bed clear', filter: 'production' },
  attention: { label: 'Order', filter: 'production' },
  stuck: { label: 'Order', filter: 'production' },
  qc: { label: 'QC', filter: 'production' },
  dry: { label: 'Filament', filter: 'production' },
  confirm: { label: 'Order', filter: 'sales' },
  ship: { label: 'Shipping', filter: 'sales' },
  leads: { label: 'Social', filter: 'sales' },
  receivable: { label: 'Finance', filter: 'sales' },
  margin: { label: 'Finance', filter: 'sales' },
  approval: { label: 'Approval', filter: 'inventory' },
  po: { label: 'Purchase', filter: 'inventory' },
  receive: { label: 'Purchase', filter: 'inventory' },
  spool_short: { label: 'Filament', filter: 'inventory' },
  spool_empty: { label: 'Filament', filter: 'inventory' },
  low_stock: { label: 'Filament', filter: 'inventory' },
  low_item: { label: 'Inventory', filter: 'inventory' },
  restock: { label: 'Inventory', filter: 'inventory' },
  backup: { label: 'System', filter: 'system' },
  dlq: { label: 'System', filter: 'system' },
  agents: { label: 'System', filter: 'system' },
}

export const FILTERS: { id: TaskFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'now', label: 'Urgent (Now)' },
  { id: 'production', label: 'Production' },
  { id: 'sales', label: 'Orders & Sales' },
  { id: 'inventory', label: 'Inventory' },
]

export function matches(item: TodayItem, f: TaskFilter): boolean {
  if (f === 'all') return true
  if (f === 'now') return item.priority === 'now'
  return CATEGORY[item.kind]?.filter === f
}

export const STAGE_SHORT: Record<string, string> = {
  LEAD: 'Lead', RECEIVED: 'Received', PERSONALIZING: 'Personalizing', SCHEDULING: 'Scheduling', PRINTING: 'Printing',
  QC: 'QC', NEEDS_ATTENTION: 'Attention',
}

/** "Mon 17" from "2026-11-17" (the chart's day labels). */
export function dayLabel(iso: string): string {
  const d = new Date(`${iso}T12:00:00`)
  return `${d.toLocaleDateString('en-GB', { weekday: 'short' })} ${d.getDate()}`
}
