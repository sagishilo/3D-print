// Sidebar navigation (groups as in the design). Screens with a `phase` are not built yet and say so; screens with a
// `minRole` are shown only to that role and above (the router and the gateway check it too).
import type { Role } from '@/lib/roles'

export interface NavItem {
  to: string
  label: string
  icon: string
  group: 'Home' | 'Sell' | 'Make' | 'Business' | 'Admin'
  phase?: number
  blurb?: string
  minRole?: Role
}

export const NAV: NavItem[] = [
  { to: '/today', label: 'Today', icon: 'inbox', group: 'Home' },
  { to: '/social', label: 'Social & Trends', icon: 'activity', group: 'Sell' },
  { to: '/catalog', label: 'Instant Catalog', icon: 'grid', group: 'Sell' },
  { to: '/niches', label: 'Niches', icon: 'tag', group: 'Sell', minRole: 'owner' },
  { to: '/pricing', label: 'Price calculator', icon: 'calc', group: 'Sell', minRole: 'operator' },
  { to: '/live', label: 'Live floor', icon: 'live', group: 'Make' },
  { to: '/orders', label: 'Order Pipeline', icon: 'columns', group: 'Make' },
  { to: '/fleet', label: 'Printer Fleet', icon: 'monitor', group: 'Make' },
  { to: '/filament', label: 'Inventory', icon: 'disc', group: 'Make' },
  { to: '/finance', label: 'Finance', icon: 'bars', group: 'Business', minRole: 'operator' },
  { to: '/scorecard', label: 'Niche scorecard', icon: 'activity', group: 'Business', minRole: 'operator' },
  { to: '/system', label: 'System & Agents', icon: 'gear', group: 'Admin', minRole: 'operator' },
  { to: '/users', label: 'Users', icon: 'users', group: 'Admin', minRole: 'owner' },
]

export const GROUPS = ['Home', 'Sell', 'Make', 'Business', 'Admin'] as const
