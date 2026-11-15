// Sidebar navigation (groups as in the design). Screens with a `phase` are not built yet and say so.
export interface NavItem {
  to: string
  label: string
  icon: string
  group: 'Sell' | 'Make' | 'Business' | 'Admin'
  phase?: number
  blurb?: string
}

export const NAV: NavItem[] = [
  { to: '/social', label: 'Social & Trends', icon: 'activity', group: 'Sell' },
  { to: '/catalog', label: 'Instant Catalog', icon: 'grid', group: 'Sell' },
  { to: '/live', label: 'Live floor', icon: 'live', group: 'Make' },
  { to: '/orders', label: 'Order Pipeline', icon: 'columns', group: 'Make' },
  { to: '/fleet', label: 'Printer Fleet', icon: 'monitor', group: 'Make' },
  { to: '/filament', label: 'Filament', icon: 'disc', group: 'Make' },
  { to: '/finance', label: 'Finance', icon: 'bars', group: 'Business' },
  { to: '/system', label: 'System & Agents', icon: 'gear', group: 'Admin' },
]

export const GROUPS = ['Sell', 'Make', 'Business', 'Admin'] as const
