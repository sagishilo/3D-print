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
