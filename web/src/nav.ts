// Sidebar navigation (groups as in the design). Screens with a `phase` are not built yet and say so; screens with a
// `minRole` are shown only to that role and above (the router and the gateway check it too).
// Labels come from the i18n catalogue (src/i18n/en.ts, key nav.<id>): the sidebar translates them live; `label` is
// the English text for code that needs a plain string (search, page titles).
import { en } from '@/i18n/en'
import type { Role } from '@/lib/roles'

export type NavId = Exclude<keyof typeof en.nav, 'groups' | 'agents'>

export interface NavItem {
  id: NavId
  to: string
  label: string
  icon: string
  group: 'Home' | 'Sell' | 'Make' | 'Business' | 'Admin'
  phase?: number
  blurb?: string
  minRole?: Role
}

const item = (id: NavId, to: string, icon: string, group: NavItem['group'], minRole?: Role): NavItem =>
  ({ id, to, label: en.nav[id], icon, group, ...(minRole ? { minRole } : {}) })

export const NAV: NavItem[] = [
  item('today', '/today', 'inbox', 'Home'),
  item('social', '/social', 'activity', 'Sell'),
  item('catalog', '/catalog', 'grid', 'Sell'),
  item('niches', '/niches', 'tag', 'Sell', 'owner'),
  item('pricing', '/pricing', 'calc', 'Sell', 'operator'),
  item('live', '/live', 'live', 'Make'),
  item('orders', '/orders', 'columns', 'Make'),
  item('fleet', '/fleet', 'monitor', 'Make'),
  item('inventory', '/filament', 'disc', 'Make'),
  item('finance', '/finance', 'bars', 'Business', 'operator'),
  item('scorecard', '/scorecard', 'activity', 'Business', 'operator'),
  item('system', '/system', 'gear', 'Admin', 'operator'),
  item('integrations', '/integrations', 'external', 'Admin', 'owner'),
  item('users', '/users', 'users', 'Admin', 'owner'),
]

export const GROUPS = ['Home', 'Sell', 'Make', 'Business', 'Admin'] as const
