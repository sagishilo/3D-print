// M7: Today (ranked work with one action each, Expert-only technical lines, approvals, all clear) and the ⌘K command
// palette (typed registry, role and mode filters, ranking, keyboard only).
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import CommandPalette from '@/components/shell/CommandPalette.vue'
import { buildActions, type ActionContext, search } from '@/lib/actions'
import { type TodayItem, type TodayList, visible, waited } from '@/lib/today'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import TodayView from '@/views/TodayView.vue'

import { json, on, serve, signedInAs } from './helpers'

const item = (over: Partial<TodayItem>): TodayItem => ({
  id: 'x', kind: 'bed', priority: 'now', title: 'Clear the bed of x1', why: 'done', min_role: 'operator', visible_to: 'viewer',
  expert: false, niche: null, age_s: 7200, count: 1, score: 302,
  action: { type: 'command', name: 'printers.clear_bed', body: { printer_id: 'x1' }, label: 'Bed cleared', confirm: 'Is it empty?' },
  ...over,
})
const LIST: TodayList = {
  now: [item({ id: 'bed:x1' }), item({ id: 'agents', kind: 'agents', expert: true, title: 'Coordinator not running',
    action: { type: 'link', to: '/system?tab=agents', label: 'Open' } })],
  today: [item({ id: 'approval:a1', kind: 'approval', priority: 'today', title: 'Order 4 x PLA', action: { type: 'approvals', label: 'Open approvals' } }),
    item({ id: 'qc:o2', kind: 'qc', priority: 'today', title: 'Check the quality of o2', action: { type: 'link', to: '/orders?order=o2', label: 'Check' } })],
  week: [], count: 4, generated_at: '2026-11-19T10:00:00Z',
}
const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs('operator')
})
afterEach(() => vi.unstubAllGlobals())

describe('today helpers', () => {
  it('hides technical lines in Simple mode and says how long something waited', () => {
    expect(visible(LIST, false).now.map((i) => i.id)).toEqual(['bed:x1'])
    expect(visible(LIST, true).now.map((i) => i.id)).toEqual(['bed:x1', 'agents'])
    expect([waited(120), waited(7200), waited(200000)]).toEqual(['2 min', '2 h', '2 d'])
  })
})

describe('the Today screen', () => {
  it('groups the work and runs an action after confirming it; the line hides at once', async () => {
    const calls = serve(on('GET', /\/api\/v1\/today/, () => json(200, LIST)),
      on('POST', '/api/v1/commands/printers.clear_bed', () => json(202, { command_id: 'c1' })))
    const ask = vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const opened = vi.fn()
    window.addEventListener('pf:open-approvals', opened)
    useAuth().prefs = { mode: 'simple' }
    const w = mount(TodayView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="today-item-bed:x1"]').text()).toContain('Clear the bed of x1')
    expect(w.get('[data-testid="today-item-bed:x1"]').text()).toContain('Bed clear')          // the category
    expect(w.find('[data-testid="today-item-agents"]').exists()).toBe(false)          // Simple mode
    expect(w.get('[data-testid="today-go-qc:o2"]').attributes('href')).toBe('/orders?order=o2')
    await w.get('[data-testid="today-act-bed:x1"]').trigger('click')
    await flushPromises()
    expect(ask).toHaveBeenCalledWith(expect.objectContaining({ text: 'Is it empty?', confirmLabel: 'Bed cleared' }))
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ printer_id: 'x1' })
    expect(w.find('[data-testid="today-item-bed:x1"]').exists()).toBe(false)
    await w.get('[data-testid="today-act-approval:a1"]').trigger('click')
    expect(opened).toHaveBeenCalled()
    window.removeEventListener('pf:open-approvals', opened)
  })

  it('offers a camera suspicion two answers: pause, or "It\'s fine" (W2-C)', async () => {
    const suspect = item({ id: 'suspect:4', kind: 'suspect', title: 'Check x1: the camera sees a likely failure',
      action: { type: 'command', name: 'printers.pause', body: { printer_id: 'x1', reason: 'camera suspicion (Today)' }, label: 'Pause', confirm: 'Pause x1?' },
      meta: { alt: { type: 'command', name: 'printers.dismiss_suspicion', body: { failure_id: 4 }, label: "It's fine", confirm: 'Dismiss?' } } })
    const calls = serve(on('GET', /\/api\/v1\/today/, () => json(200, { ...LIST, now: [suspect], today: [] })),
      on('POST', /\/api\/v1\/commands\//, () => json(202, { command_id: 'c' })))
    vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const w = mount(TodayView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="today-item-suspect:4"]').text()).toContain('Camera')
    await w.get('[data-testid="today-alt-suspect:4"]').trigger('click')
    await flushPromises()
    const post = calls.find((c) => c.method === 'POST')
    expect([post?.url.split('/').at(-1), post?.body]).toEqual(['printers.dismiss_suspicion', { failure_id: 4 }])
  })

  it('says all clear when nothing waits', async () => {
    serve(on('GET', /\/api\/v1\/today/, () => json(200, { now: [], today: [], week: [], count: 0, generated_at: '' })))
    const w = mount(TodayView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.text()).toContain('All clear')
  })
})

function ctx(over: Partial<ActionContext> = {}): ActionContext {
  return {
    can: (r) => r !== 'owner', expert: false, go: vi.fn(), screens: [{ to: '/live', label: 'Live floor' }, { to: '/users', label: 'Users', minRole: 'owner' }],
    printers: [{ id: 'bambu-01', status: 'printing' }, { id: 'voron-01', status: 'paused' }],
    orders: [{ order_id: 'ord-1234abcd', customer_name: 'Noa Cohen', state: 'QC' }],
    products: [{ sku: 'HOOK-A', name: 'Wall hook', active: true }], niches: [{ slug: 'kids', name: 'Kids & toys' }],
    agents: [{ name: 'fleet', label: 'Fleet Manager' }], currentTour: 'live',
    pausePrinter: vi.fn(), resumePrinter: vi.fn(), pauseAll: vi.fn(), setNiche: vi.fn(), setPrefs: vi.fn(), startTour: vi.fn(),
    openApprovals: vi.fn(), ...over,
  }
}

describe('the action registry', () => {
  it('knows screens, actions, printers, orders, products, niches, display and tours; filtered by role and mode', () => {
    const ids = buildActions(ctx()).map((a) => a.id)
    expect(ids).toEqual(expect.arrayContaining(['go:/live', 'new-order', 'pause:bambu-01', 'resume:voron-01', 'order:ord-1234abcd',
      'sku:HOOK-A', 'niche:kids', 'theme:dark', 'mode:expert', 'tour:page']))
    expect(ids).not.toContain('go:/users')                                     // owners only
    expect(ids).not.toContain('agent:fleet')                                  // Expert mode only
    expect(buildActions(ctx({ expert: true })).map((a) => a.id)).toContain('agent:fleet')
    const viewer = buildActions(ctx({ can: () => false })).map((a) => a.id)
    expect(viewer).not.toContain('new-order')
    expect(viewer).not.toContain('pause:bambu-01')
  })

  it('ranks what the words match', () => {
    const all = buildActions(ctx())
    expect(search(all, 'new ord')[0]?.id).toBe('new-order')
    expect(search(all, 'noa')[0]?.id).toBe('order:ord-1234abcd')
    expect(search(all, 'hook')[0]?.id).toBe('sku:HOOK-A')
    expect(search(all, 'pause bambu')[0]?.id).toBe('pause:bambu-01')
    expect(search(all, 'dark')[0]?.id).toBe('theme:dark')
    expect(search(all, 'zzz')).toEqual([])
    expect(search(all, '').every((a) => a.group === 'Actions' || a.group === 'Go to')).toBe(true)
  })
})

describe('the palette', () => {
  it('is driven by the keyboard alone', async () => {
    const calls = serve(on('PATCH', '/api/v1/auth/prefs', (c) => json(200, { prefs: c.body })),
      on('GET', /\/api\/v1\//, () => json(200, { orders: [], products: [], categories: [], counts: {}, niches: [], plates: [] })))
    const router = anyRoute()
    const push = vi.spyOn(router, 'push')
    const w = mount(CommandPalette, { props: { open: false, 'onUpdate:open': (v: boolean) => w.setProps({ open: v }) },
      global: { plugins: [router] }, attachTo: document.body })
    await w.setProps({ open: true })
    await flushPromises()
    const input = w.get('[data-testid="palette-input"]')
    await input.setValue('dark')
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ theme: 'dark' })
    expect(w.find('[data-testid="palette"]').exists()).toBe(false)              // closed after running
    await w.setProps({ open: true })
    await flushPromises()
    await w.get('[data-testid="palette-input"]').setValue('new order')
    await w.get('[data-testid="palette-input"]').trigger('keydown', { key: 'ArrowDown' })
    await w.get('[data-testid="palette-input"]').trigger('keydown', { key: 'ArrowUp' })
    await w.get('[data-testid="palette-input"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(push).toHaveBeenCalledWith('/orders?new=1')
    w.unmount()
  })
})

const SUMMARY = {
  printers: { total: 4, online: 4, printing: 3, utilization_pct: 75 }, orders_today: 5, revenue_today_agorot: 42000,
  alerts: { filament: 1, parts: 2, shelf: 0 },
  week: ['2026-11-13', '2026-11-14', '2026-11-15', '2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19']
    .map((day, i) => ({ day, orders: i, revenue_agorot: i * 1000 })),
  stages: [{ state: 'LEAD', count: 2 }, { state: 'QC', count: 4 }, { state: 'PRINTING', count: 0 }],
  niches: [{ niche: 'gifts', name: 'Personalised gifts', color: 4, revenue_agorot: 21000 }], money: true,
}

describe('the redesigned Today', () => {
  it('shows the KPI strip, the 7-day charts and where orders pile up', async () => {
    serve(on('GET', '/api/v1/today', () => json(200, LIST)), on('GET', '/api/v1/today/summary', () => json(200, SUMMARY)))
    useAuth().prefs = { mode: 'expert' }
    const w = mount(TodayView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="kpi-printers"]').text()).toContain('3/4')
    expect(w.get('[data-testid="kpi-printers"]').text()).toContain('75% busy')
    expect(w.get('[data-testid="kpi-urgent"]').text()).toContain('2')                 // Expert: the bed and the agents
    expect(w.get('[data-testid="kpi-orders"]').text()).toContain('5')
    expect(w.get('[data-testid="kpi-orders"]').text()).toContain('₪420')
    expect(w.get('[data-testid="kpi-stock"]').text()).toContain('1 filament · 2 parts · 0 shelf')
    expect(w.get('[data-testid="chart-revenue"]').text()).toContain('₪210')           // the week's total
    expect(w.get('[data-testid="chart-orders"]').find('svg').attributes('aria-label')).toContain('6')
    expect(w.find('[data-testid="stages-QC"]').text()).toContain('4')
    expect(w.find('[data-testid="stages-PRINTING"]').exists()).toBe(false)            // empty stages are left out
    await w.get('[data-testid="breakdown-niche"]').trigger('click')
    expect(w.get('[data-testid="niches-gifts"]').text()).toContain('Personalised gifts')
    // a table view of the same numbers
    await w.get('[data-testid="chart-orders"]').get('button').trigger('click')
    expect(w.get('[data-testid="chart-orders"]').find('table').exists()).toBe(true)
  })

  it('filters the tasks in one click, and the urgent KPI is a shortcut', async () => {
    serve(on('GET', '/api/v1/today', () => json(200, LIST)), on('GET', '/api/v1/today/summary', () => json(200, SUMMARY)))
    useAuth().prefs = { mode: 'simple' }
    const w = mount(TodayView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="filter-now"]').text()).toContain('1')
    await w.get('[data-testid="filter-production"]').trigger('click')
    expect(w.findAll('[data-testid^="today-item-"]').map((x) => x.attributes('data-testid'))).toEqual(['today-item-bed:x1', 'today-item-qc:o2'])
    await w.get('[data-testid="filter-sales"]').trigger('click')
    expect(w.get('[data-testid="filter-empty"]').text()).toContain('Nothing in this filter')
    await w.get('[data-testid="kpi-urgent"]').trigger('click')
    expect(w.findAll('[data-testid^="today-item-"]').map((x) => x.attributes('data-testid'))).toEqual(['today-item-bed:x1'])
  })

  it('shows viewers no money', async () => {
    signedInAs('viewer')
    serve(on('GET', '/api/v1/today', () => json(200, LIST)),
      on('GET', '/api/v1/today/summary', () => json(200, { ...SUMMARY, money: false, revenue_today_agorot: null, niches: [] })))
    const w = mount(TodayView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.find('[data-testid="chart-revenue"]').exists()).toBe(false)
    expect(w.find('[data-testid="breakdown-niche"]').exists()).toBe(false)
    expect(w.get('[data-testid="kpi-orders"]').text()).not.toContain('₪')
  })
})
