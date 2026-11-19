// The command palette's actions (Wave 1 / M7): one typed registry of everything a person can go to or do from
// ⌘K. Built from what the dashboard already knows (screens, printers, open orders, products, niches) and filtered by
// role and mode; `search` ranks them for a query. Running an action is the palette's job (components/shell).
import type { Role } from '@/lib/roles'

export type ActionGroup = 'Go to' | 'Actions' | 'Printers' | 'Orders' | 'Products' | 'Niche' | 'Display' | 'Help' | 'Agents'

export interface PaletteAction {
  id: string
  label: string
  hint: string
  group: ActionGroup
  keywords?: string
  minRole?: Role
  expert?: boolean
  /** Asked before running (actions that change something on the farm). */
  confirm?: { title: string; text: string; label: string; danger?: boolean }
  run: () => unknown
}

export interface ActionContext {
  can: (role: Role) => boolean
  expert: boolean
  go: (to: string) => unknown
  screens: { to: string; label: string; minRole?: Role }[]
  printers: { id: string; status: string }[]
  orders: { order_id: string; customer_name: string; state: string }[]
  products: { sku: string; name: string; active: boolean }[]
  niches: { slug: string; name: string }[]
  agents: { name: string; label: string }[]
  currentTour: string | null
  pausePrinter: (id: string) => unknown
  resumePrinter: (id: string) => unknown
  pauseAll: () => unknown
  setNiche: (slug: string | null) => unknown
  setPrefs: (p: Record<string, string>) => unknown
  startTour: (id: string) => unknown
  openApprovals: () => unknown
}

export function buildActions(c: ActionContext): PaletteAction[] {
  const out: PaletteAction[] = []
  for (const s of c.screens) {
    out.push({ id: `go:${s.to}`, label: s.label, hint: 'screen', group: 'Go to', minRole: s.minRole, keywords: s.to, run: () => c.go(s.to) })
  }
  out.push(
    { id: 'new-order', label: 'New order', hint: 'Order Pipeline', group: 'Actions', minRole: 'operator', keywords: 'create manual customer sale', run: () => c.go('/orders?new=1') },
    { id: 'quote', label: 'Price a quote', hint: 'Price calculator', group: 'Actions', minRole: 'operator', keywords: 'price calculator cost whatsapp', run: () => c.go('/pricing') },
    { id: 'import-3mf', label: 'Import a 3MF', hint: 'Instant Catalog', group: 'Actions', minRole: 'operator', keywords: 'new product sku upload', run: () => c.go('/catalog?new=1') },
    { id: 'record-payment', label: 'Record a payment', hint: 'Finance', group: 'Actions', minRole: 'operator', keywords: 'money income paid bit', run: () => c.go('/finance?new=payment') },
    { id: 'add-expense', label: 'Add an expense', hint: 'Finance', group: 'Actions', minRole: 'operator', keywords: 'money cost bill receipt', run: () => c.go('/finance?new=expense') },
    { id: 'approvals', label: 'Open approvals', hint: 'inbox', group: 'Actions', keywords: 'approve reject purchase', run: () => c.openApprovals() },
    { id: 'pause-all', label: 'Pause all printers', hint: 'every printer printing now', group: 'Actions', minRole: 'operator', keywords: 'stop emergency',
      confirm: { title: 'Pause all printers?', text: 'Every printer that is printing now is paused; each pause is verified.', label: 'Pause all', danger: true },
      run: () => c.pauseAll() },
  )
  for (const p of c.printers) {
    out.push({ id: `printer:${p.id}`, label: p.id, hint: `printer · ${p.status}`, group: 'Printers', keywords: 'printer fleet', run: () => c.go(`/fleet?printer=${encodeURIComponent(p.id)}`) })
    if (p.status === 'printing' || p.status === 'heating') {
      out.push({ id: `pause:${p.id}`, label: `Pause ${p.id}`, hint: 'printer', group: 'Printers', minRole: 'operator', keywords: 'stop hold',
        confirm: { title: `Pause ${p.id}?`, text: 'The print pauses where it is; resume it from here or the printer.', label: 'Pause' }, run: () => c.pausePrinter(p.id) })
    }
    if (p.status === 'paused') {
      out.push({ id: `resume:${p.id}`, label: `Resume ${p.id}`, hint: 'printer', group: 'Printers', minRole: 'operator', keywords: 'continue start',
        confirm: { title: `Resume ${p.id}?`, text: 'The print continues.', label: 'Resume' }, run: () => c.resumePrinter(p.id) })
    }
  }
  for (const o of c.orders) {
    out.push({ id: `order:${o.order_id}`, label: `#${o.order_id.replace(/^ord-/, '')} ${o.customer_name}`, hint: `order · ${o.state.toLowerCase().replace('_', ' ')}`,
      group: 'Orders', keywords: `${o.order_id} order customer`, run: () => c.go(`/orders?order=${encodeURIComponent(o.order_id)}`) })
  }
  for (const p of c.products.filter((x) => x.active)) {
    out.push({ id: `sku:${p.sku}`, label: `${p.name}`, hint: `${p.sku} · quote it`, group: 'Products', minRole: 'operator', keywords: `${p.sku} product sku price`,
      run: () => c.go(`/pricing?sku=${encodeURIComponent(p.sku)}`) })
  }
  out.push({ id: 'niche:all', label: 'All niches', hint: 'show everything', group: 'Niche', keywords: 'niche filter show all', run: () => c.setNiche(null) })
  for (const n of c.niches) {
    out.push({ id: `niche:${n.slug}`, label: `Focus on ${n.name}`, hint: 'niche', group: 'Niche', keywords: `niche filter ${n.slug}`, run: () => c.setNiche(n.slug) })
  }
  for (const [theme, label] of [['light', 'Light theme'], ['dark', 'Dark theme'], ['system', 'Theme like the computer']] as const) {
    out.push({ id: `theme:${theme}`, label, hint: 'display', group: 'Display', keywords: 'theme colours appearance', run: () => c.setPrefs({ theme }) })
  }
  out.push(
    { id: 'density:compact', label: 'Compact density', hint: 'display', group: 'Display', keywords: 'dense small rows', run: () => c.setPrefs({ density: 'compact' }) },
    { id: 'density:comfortable', label: 'Comfortable density', hint: 'display', group: 'Display', keywords: 'roomy large', run: () => c.setPrefs({ density: 'comfortable' }) },
    { id: 'mode:simple', label: 'Simple mode', hint: 'hide the technical parts', group: 'Display', keywords: 'mode easy', run: () => c.setPrefs({ mode: 'simple' }) },
    { id: 'mode:expert', label: 'Expert mode', hint: 'show everything', group: 'Display', keywords: 'mode advanced technical', run: () => c.setPrefs({ mode: 'expert' }) },
    { id: 'tour:general', label: 'Start the guided tour', hint: 'help', group: 'Help', keywords: 'tour help guide learn', run: () => c.startTour('general') },
    { id: 'help', label: 'User guide', hint: 'help', group: 'Help', keywords: 'guide manual hebrew', run: () => c.go('/help') },
  )
  if (c.currentTour) {
    out.push({ id: 'tour:page', label: 'Tour of this screen', hint: 'help', group: 'Help', keywords: 'tour help page', run: () => c.startTour(c.currentTour!) })
  }
  for (const a of c.agents) {
    out.push({ id: `agent:${a.name}`, label: a.label, hint: 'agent', group: 'Agents', expert: true, minRole: 'operator', keywords: 'agent system', run: () => c.go('/system?tab=agents') })
  }
  return out.filter((a) => (!a.minRole || c.can(a.minRole)) && (!a.expert || c.expert))
}

const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')

/** Actions matching every word of the query, best first: label starts with it, then a word starts with it. */
export function search(actions: PaletteAction[], query: string, limit = 12): PaletteAction[] {
  const words = norm(query).split(/\s+/).filter(Boolean)
  if (!words.length) return actions.filter((a) => a.group === 'Actions' || a.group === 'Go to').slice(0, limit)
  const scored: { a: PaletteAction; score: number }[] = []
  for (const a of actions) {
    const label = norm(a.label)
    const hay = `${label} ${norm(a.hint)} ${norm(a.keywords ?? '')}`
    if (!words.every((w) => hay.includes(w))) continue
    let score = 0
    if (label.startsWith(words[0]!)) score += 3
    if (label.split(/[\s#·-]+/).some((t) => t.startsWith(words[0]!))) score += 2
    if (label.includes(words.join(' '))) score += 1
    if (a.group === 'Actions' || a.group === 'Go to') score += 0.5
    scored.push({ a, score })
  }
  return scored.sort((x, y) => y.score - x.score || x.a.label.localeCompare(y.a.label)).slice(0, limit).map((x) => x.a)
}
