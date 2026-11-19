// Sidebar navigation (groups as in the design). Screens with a `phase` are not built yet and say so; screens with a
// `minRole` are shown only to that role and above (the router and the gateway check it too).
import type { Role } from '@/lib/roles'

export interface NavItem {
  to: string
  label: string
  icon: string
  group: 'Sell' | 'Make' | 'Business' | 'Admin'
  phase?: number
  blurb?: string
  minRole?: Role
}

export const NAV: NavItem[] = [
  { to: '/social', label: 'Social & Trends', icon: 'activity', group: 'Sell' },
  { to: '/catalog', label: 'Instant Catalog', icon: 'grid', group: 'Sell' },
  { to: '/niches', label: 'Niches', icon: 'tag', group: 'Sell', minRole: 'owner' },
  { to: '/live', label: 'Live floor', icon: 'live', group: 'Make' },
  { to: '/orders', label: 'Order Pipeline', icon: 'columns', group: 'Make' },
  { to: '/fleet', label: 'Printer Fleet', icon: 'monitor', group: 'Make' },
  { to: '/filament', label: 'Filament', icon: 'disc', group: 'Make' },
  { to: '/finance', label: 'Finance', icon: 'bars', group: 'Business', minRole: 'operator' },
  { to: '/system', label: 'System & Agents', icon: 'gear', group: 'Admin', minRole: 'operator' },
  { to: '/users', label: 'Users', icon: 'users', group: 'Admin', minRole: 'owner' },
]

export const GROUPS = ['Sell', 'Make', 'Business', 'Admin'] as const
