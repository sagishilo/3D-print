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
  {
    to: '/social',
    label: 'Social & Trends',
    icon: 'activity',
    group: 'Sell',
    phase: 10,
    blurb: 'Buying-intent posts captured by group members, AI-drafted replies you approve and post yourself, and demand trends.',
  },
  {
    to: '/catalog',
    label: 'Instant Catalog',
    icon: 'grid',
    group: 'Sell',
    phase: 8,
    blurb: 'Sliced 3MF templates and generators, ranked by demand, queued as print batches.',
  },
  { to: '/live', label: 'Live floor', icon: 'live', group: 'Make' },
  { to: '/orders', label: 'Order Pipeline', icon: 'columns', group: 'Make' },
  { to: '/fleet', label: 'Printer Fleet', icon: 'monitor', group: 'Make' },
  { to: '/filament', label: 'Filament', icon: 'disc', group: 'Make' },
  {
    to: '/finance',
    label: 'Finance',
    icon: 'bars',
    group: 'Business',
    phase: 7,
    blurb: 'Income, expenses, VAT, product margins and receivables from the ledger.',
  },
  { to: '/system', label: 'System & Agents', icon: 'gear', group: 'Admin' },
]

export const GROUPS = ['Sell', 'Make', 'Business', 'Admin'] as const
