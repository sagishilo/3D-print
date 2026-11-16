// Page tours (הדרכות עמוד): each screen's deep dive covers what it promises, in plain Hebrew, anchored to elements
// and labels that exist; the button starts it; the overlay prepares the page (opens a drawer, ticks a printer) and
// leaves it as it was when the tour ends.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'

import calibration from '../../src/printfarm/contracts/calibration.py?raw'

import TopBar from '@/components/shell/TopBar.vue'
import PageTourButton from '@/components/tour/PageTourButton.vue'
import TourOverlay from '@/components/tour/TourOverlay.vue'
import { PAGE_TOURS, TOURS, type TourId } from '@/lib/page-tours'
import { NAV } from '@/nav'
import { routes } from '@/router'
import type { TourStep } from '@/lib/tour'
import { useTour } from '@/stores/tour'

const files = {
  ...(import.meta.glob('../src/**/*.vue', { query: '?raw', import: 'default', eager: true }) as Record<string, string>),
  ...(import.meta.glob('../src/lib/*.ts', { query: '?raw', import: 'default', eager: true }) as Record<string, string>),
}
const SOURCE = Object.entries(files)
  .filter(([path]) => !/tour/.test(path))
  .map(([, text]) => text)
  .join('\n') + calibration

const ALL = Object.values(files).join('\n')
/** data-x="value" written literally, or by a template such as :data-testid="`column-${col.stage}`" or :data-tab="t.id". */
function rendered(name: string, value: string): boolean {
  if (ALL.includes(`${name}="${value}"`)) return true
  if (name === 'data-tab') return ALL.includes(':data-tab="t.id"')
  const prefixes = [...ALL.matchAll(new RegExp(':' + name + '="`([^$`]+)\\$\\{', 'g'))].map((m) => m[1])
  return prefixes.some((p) => value.startsWith(p))
}

const text = (tour: TourId, id: string) => {
  const s = TOURS[tour].steps.find((x) => x.id === id)
  expect(s, `${tour}/${id}`).toBeTruthy()
  return [s!.title, ...s!.body, s!.why ?? '', s!.missing ?? ''].join(' ')
}

describe('page tours', () => {
  it('exist for every screen of the dashboard and stay on their own screen', () => {
    // Every logged-in route (the sign-in page has no shell) names its page tour; every page tour belongs to a route.
    const shell = routes.find((r) => r.path === '/')!.children!.filter((r) => r.component)
    expect(shell.length).toBeGreaterThanOrEqual(9)
    const byRoute = new Map(shell.map((r) => [`/${r.path}`, r.meta?.tour]))
    for (const [path, tour] of byRoute) {
      expect(tour, `${path} has a page tour`).toBeTruthy()
      expect(PAGE_TOURS, path).toContain(tour)
    }
    expect([...byRoute.values()].sort()).toEqual([...PAGE_TOURS].sort())
    for (const n of NAV) expect(byRoute.get(n.to), `sidebar ${n.to}`).toBeTruthy()

    for (const id of PAGE_TOURS) {
      const t = TOURS[id]
      const path = [...byRoute].find(([, tour]) => tour === id)![0]
      expect(t.title).toContain('מדריך לעמוד')
      expect(t.steps.length, id).toBeGreaterThanOrEqual(4)
      for (const s of t.steps) expect(s.route, `${id}/${s.id}`).toBe(path)
      expect(new Set(t.steps.map((s) => s.id)).size).toBe(t.steps.length)
      // Each starts at the "מדריך לעמוד" button that opened it.
      expect(t.steps[0].targets).toEqual([`[data-testid="page-tour-${id}"]`])
    }
    for (const id of ['live', 'orders', 'fleet'] as const) expect(TOURS[id].steps.length).toBeGreaterThanOrEqual(10)
  })

  it('are plain Hebrew with a "why it matters" on every step', () => {
    for (const id of PAGE_TOURS)
      for (const s of TOURS[id].steps) {
        expect(s.title, s.id).toMatch(/[֐-׿]/)
        for (const line of s.body) expect(line, s.id).toMatch(/[֐-׿]/)
        expect(s.why, s.id).toMatch(/[֐-׿]/)
      }
  })

  it('every element they point at or click is rendered by the dashboard', () => {
    const tours: TourStep[][] = Object.values(TOURS).map((t) => t.steps)
    for (const steps of tours)
      for (const s of steps) {
        const sels = [...s.targets, ...(s.prepare ?? []).flatMap((a) => [a.click, a.unless ?? '', a.undo?.click ?? ''])].filter(Boolean)
        for (const sel of sels) {
          const attrs = [...sel.matchAll(/\[(data-[a-z]+)="([^"]+)"\]/g)]
          expect(attrs.length, `${s.id}: ${sel}`).toBeGreaterThan(0)
          for (const [, name, value] of attrs) expect(rendered(name, value), `${s.id}: ${sel}`).toBe(true)
        }
      }
  })

  it('name screen labels exactly as the dashboard shows them', () => {
    // Bold English words are screen labels (or the vendor terms the screens show); each must exist in the app.
    const CONCEPTS = new Set(['AMS', 'HMS', 'PID', 'ETA', 'slot', 'plate', 'Channel', 'Due', 'LIVE', 'CONNECTING',
      'Live · 0.4 s ago', // an example of the live indicator's text, which changes every second
    ])
    const missing: string[] = []
    for (const id of PAGE_TOURS)
      for (const s of TOURS[id].steps)
        for (const line of [...s.body, s.why ?? '', s.missing ?? ''])
          for (const [, label] of line.matchAll(/\*\*([^*]+)\*\*/g))
            if (!/[֐-׿]/.test(label) && !CONCEPTS.has(label) && !SOURCE.includes(label)) missing.push(`${id}/${s.id}: ${label}`)
    expect(missing).toEqual([])
  })

  it('Live covers connection, Pause all, tiles, progress, Bed cleared and cameras', () => {
    const all = TOURS.live.steps.map((s) => s.id).join(' ')
    for (const id of ['live-connection', 'live-pause-all', 'live-tile', 'live-progress', 'live-bed-cleared', 'live-camera']) expect(all).toContain(id)
    expect(text('live', 'live-bed-cleared')).toContain('**Bed cleared**')
    expect(text('live', 'live-pause-all')).toContain('**Resume**')
  })

  it('Orders covers the 7 columns, New manual order, dragging, auto-advance and failed prints', () => {
    const board = text('orders', 'orders-board')
    for (const col of ['Lead', 'Order received', 'Personalization', 'Slicing / scheduling', 'Printing', 'Quality control', 'Shipped'])
      expect(board).toContain(`**${col}**`)
    expect(text('orders', 'orders-new')).toContain('**New manual order**')
    expect(text('orders', 'orders-drag')).toContain('גוררים')
    expect(text('orders', 'orders-auto')).toContain('**off**')
    expect(text('orders', 'orders-failed')).toContain('**Fail · reprint**')
  })

  it('Fleet covers filters, the details panel, batch actions and calibration with its safety check', () => {
    expect(text('fleet', 'fleet-filters')).toContain('**All**')
    expect(text('fleet', 'fleet-temps')).toContain('**Temperatures**')
    expect(text('fleet', 'fleet-ams')).toContain('**AMS**')
    expect(text('fleet', 'fleet-connection')).toContain('**Wi-Fi**')
    expect(text('fleet', 'fleet-health')).toContain('**HMS**')
    for (const b of ['Pause', 'Resume', 'Cancel print', 'Bed cleared', 'Home axes']) expect(text('fleet', 'fleet-batch')).toContain(`**${b}**`)
    const cal = text('fleet', 'fleet-calibration')
    for (const r of ['Bed leveling', 'Vibration compensation', 'Nozzle PID tuning', 'Bed PID tuning']) expect(cal).toContain(`**${r}**`)
    expect(cal).toContain('Input Shaper')
    // The exact statement the operator ticks before a calibration starts.
    expect(text('fleet', 'fleet-calibration-safety')).toContain('**The bed is empty and nothing blocks the print head**')
    expect(files['../src/components/fleet/CalibrationPanel.vue']).toContain("requireCheck: 'The bed is empty and nothing blocks the print head.'")
  })

  it('Fleet opens the details panel for its steps and closes it again', () => {
    const steps = TOURS.fleet.steps
    const drawerSteps = steps.filter((s) => s.targets.some((t) => t.includes('printer-drawer')))
    expect(drawerSteps.length).toBeGreaterThanOrEqual(6)
    for (const s of drawerSteps) expect(s.prepare?.[0].click, s.id).toBe('[data-tour="tile-details"]')
    for (const s of steps.filter((x) => !drawerSteps.includes(x))) expect(s.prepare?.[0].click, s.id).toContain('Close details')
  })
})

describe('tour store with several tours', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('starts a page tour and remembers each tour separately', () => {
    const t = useTour()
    t.start('orders')
    expect(t.title).toBe('מדריך לעמוד · Order Pipeline')
    expect(t.step?.id).toBe('orders-intro')
    t.start('orders', TOURS.orders.steps.length - 1)
    t.next()
    expect(t.seen('orders')).toBe(true)
    expect(t.seen('live')).toBe(false)
    expect(t.seen()).toBe(false)
  })
})

describe('PageTourButton', () => {
  it('starts the tour of its screen', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const w = mount(PageTourButton, { props: { tour: 'fleet' }, global: { plugins: [pinia] } })
    expect(w.attributes('data-testid')).toBe('page-tour-fleet')
    expect(w.text()).toContain('מדריך לעמוד')
    await w.trigger('click')
    expect(useTour().tourId).toBe('fleet')
    expect(useTour().step?.id).toBe('fleet-intro')
  })
})

describe('the top bar', () => {
  it('shows the tour button of the current screen, at the same place on every screen', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({ history: createMemoryHistory(), routes: routes.find((r) => r.path === '/')!.children!.filter((r) => r.component).map((r) => ({ path: `/${r.path}`, meta: r.meta, component: { render: () => null } })) })
    for (const id of PAGE_TOURS) {
      const path = routes.find((r) => r.path === '/')!.children!.find((r) => r.meta?.tour === id)!.path
      await router.push(`/${path}`)
      const w = mount(TopBar, { global: { plugins: [router, pinia] } })
      const buttons = w.findAll('[data-testid^="page-tour-"]')
      expect(buttons.map((b) => b.attributes('data-testid')), path).toEqual([`page-tour-${id}`])
      // The last control of the top bar, at its far right.
      const right = w.find('.right')
      expect(right.element.lastElementChild?.getAttribute('data-testid'), path).toBe(`page-tour-${id}`)
      await buttons[0].trigger('click')
      expect(useTour().tourId).toBe(id)
      useTour().stop()
      w.unmount()
    }
  })
})

describe('TourOverlay prepares the page', () => {
  it('opens the details panel, switches its tab, and closes it when the tour ends', async () => {
    const open = ref(false)
    const tabSel = ref('overview')
    const Fleet = defineComponent({
      setup: () => () =>
        h('div', [
          h('button', { 'data-testid': 'page-tour-fleet' }, 'tour'),
          h('div', { 'data-tour': 'fleet-filters' }, 'filters'),
          h('article', { 'data-tour': 'printer-tile' }, [
            h('label', { 'data-tour': 'tile-select' }, [h('input', { type: 'checkbox' })]),
            h('button', { 'data-tour': 'tile-details', onClick: () => (open.value = true) }, 'Details'),
          ]),
          open.value
            ? h('aside', { 'data-testid': 'printer-drawer' }, [
                h('button', { 'aria-label': 'Close details', onClick: () => (open.value = false) }, 'x'),
                ...['overview', 'hardware', 'calibration'].map((id) =>
                  h('button', { 'data-tab': id, 'aria-selected': String(tabSel.value === id), onClick: () => (tabSel.value = id) }, id),
                ),
                h('div', { 'data-testid': 'hw-temps' }, 'temps'),
              ])
            : null,
        ]),
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/fleet', component: Fleet }, { path: '/:any(.*)', component: Fleet }] })
    await router.push('/fleet')
    const pinia = createPinia()
    setActivePinia(pinia)
    const w = mount(defineComponent({ render: () => h('div', [h(TourOverlay), h(RouterView)]) }), { global: { plugins: [router, pinia] }, attachTo: document.body })
    const tour = useTour()
    const at = (id: string) => TOURS.fleet.steps.findIndex((s) => s.id === id)

    tour.start('fleet', at('fleet-temps'))
    await vi_wait(() => open.value && tabSel.value === 'hardware')
    expect(open.value).toBe(true)
    expect(tabSel.value).toBe('hardware')

    tour.start('fleet', at('fleet-select')) // a step outside the drawer closes it
    await vi_wait(() => !open.value)

    tour.start('fleet', at('fleet-drawer'))
    await vi_wait(() => open.value && tabSel.value === 'overview')
    tour.stop() // the tour opened the drawer: ending the tour closes it
    await flushPromises()
    expect(open.value).toBe(false)
    w.unmount()
  })
})

async function vi_wait(cond: () => boolean, ms = 3000) {
  const until = Date.now() + ms
  while (!cond()) {
    if (Date.now() > until) throw new Error('condition not met in time')
    await new Promise((r) => setTimeout(r, 20))
    await flushPromises()
  }
}
