// The guided tour: its steps (complete, in plain Hebrew, anchored to elements that exist), the bubble placement and the
// overlay itself (moves between screens, locks the page, keyboard).
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'

import TourOverlay from '@/components/tour/TourOverlay.vue'
import { findTarget, placeBubble, richText, TOUR_STEPS } from '@/lib/tour'
import { useTour } from '@/stores/tour'

const VIEW = { width: 1440, height: 900 }
const BUBBLE = { width: 380, height: 300 }

describe('tour steps', () => {
  it('cover the screens the operator needs, in order', () => {
    const ids = TOUR_STEPS.map((s) => s.id)
    const order = ['sidebar', 'pause-all', 'printer-tile', 'orders', 'social']
    for (const id of order) expect(ids).toContain(id)
    for (let i = 1; i < order.length; i++) expect(ids.indexOf(order[i - 1])).toBeLessThan(ids.indexOf(order[i]))
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('are written in Hebrew, explain why, and name the real buttons', () => {
    for (const s of TOUR_STEPS) {
      expect(s.title, s.id).toMatch(/[֐-׿]/)
      for (const line of s.body) expect(line, s.id).toMatch(/[֐-׿]/)
      expect(s.why, s.id).toBeTruthy()
    }
    const text = (id: string) => TOUR_STEPS.find((s) => s.id === id)!.body.join(' ')
    for (const g of ['Sell', 'Make', 'Business', 'Admin']) expect(text('sidebar')).toContain(`**${g}**`)
    for (const b of ['Pause', 'Resume', 'Bed cleared']) expect(text('printer-tile')).toContain(`**${b}**`)
    expect(text('social')).toContain('**Approve & Copy**')
    expect(text('social')).toContain('**Mark as posted**')
  })

  it('anchor to attributes the dashboard renders', () => {
    const files = import.meta.glob('../src/**/*.vue', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
    const source = Object.values(files).join('\n')
    for (const s of TOUR_STEPS)
      for (const sel of s.targets) {
        const attr = /\[(data-[a-z]+)="([^"]+)"\]/.exec(sel)
        expect(attr, sel).toBeTruthy()
        expect(source, sel).toContain(`${attr![1]}="${attr![2]}"`)
      }
  })

  it('a step whose element can be missing says what the operator would see', () => {
    for (const id of ['printer-tile', 'social', 'orders']) expect(TOUR_STEPS.find((s) => s.id === id)!.missing).toBeTruthy()
  })
})

describe('richText', () => {
  it('bolds **text** and escapes markup', () => {
    expect(richText('לחצו **Pause** <b>x</b>')).toBe('לחצו <strong>Pause</strong> &lt;b&gt;x&lt;/b&gt;')
  })
})

describe('placeBubble', () => {
  it('goes below an element near the top, kept on screen', () => {
    const p = placeBubble({ top: 20, left: 1200, width: 160, height: 40 }, BUBBLE, VIEW)
    expect(p.side).toBe('bottom')
    expect(p.top).toBe(20 + 40 + 14)
    expect(p.left + BUBBLE.width).toBeLessThanOrEqual(VIEW.width - 12)
  })

  it('goes above an element near the bottom', () => {
    const p = placeBubble({ top: 800, left: 400, width: 200, height: 60 }, BUBBLE, VIEW)
    expect(p.side).toBe('top')
    expect(p.top + BUBBLE.height).toBeLessThanOrEqual(800)
  })

  it('goes beside a tall element (the sidebar)', () => {
    const p = placeBubble({ top: 0, left: 0, width: 236, height: 900 }, BUBBLE, VIEW)
    expect(p.side).toBe('right')
    expect(p.left).toBeGreaterThanOrEqual(236)
  })

  it('stays on screen while the element is still below the fold', () => {
    const p = placeBubble({ top: 1000, left: 20, width: 350, height: 300 }, { width: 366, height: 500 }, { width: 390, height: 844 })
    expect(p.top).toBeGreaterThanOrEqual(12)
    expect(p.top + 500).toBeLessThanOrEqual(844 - 12)
  })

  it('is centred without an element, and stays inside a phone screen', () => {
    expect(placeBubble(null, BUBBLE, VIEW).side).toBe('center')
    const phone = { width: 390, height: 844 }
    const p = placeBubble({ top: 0, left: 0, width: 390, height: 844 }, { width: 366, height: 320 }, phone)
    expect(p.left).toBeGreaterThanOrEqual(12)
    expect(p.top + 320).toBeLessThanOrEqual(phone.height - 12)
  })
})

describe('findTarget', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('skips invisible matches and falls back to the next selector', () => {
    document.body.innerHTML = '<div data-a="1" id="hidden"></div><div data-b="1" id="shown"></div>'
    const shown = document.getElementById('shown')!
    shown.getBoundingClientRect = () => ({ width: 10, height: 10, top: 0, left: 0 }) as DOMRect
    expect(findTarget(['[data-a="1"]', '[data-b="1"]'])?.id).toBe('shown')
    expect(findTarget(['[data-a="1"]'])).toBeNull()
    expect(findTarget(['::not-a-selector(', '[data-b="1"]'])?.id).toBe('shown')
  })
})

describe('tour store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('walks the steps and remembers that the tour was finished', () => {
    const t = useTour()
    expect(t.active).toBe(false)
    t.start()
    expect(t.step?.id).toBe('welcome')
    t.prev()
    expect(t.index).toBe(0)
    for (let i = 1; i < t.steps.length; i++) t.next()
    expect(t.isLast).toBe(true)
    expect(t.seen()).toBe(false)
    t.next()
    expect(t.active).toBe(false)
    expect(t.seen()).toBe(true)
  })

  it('skipping does not count as finished', () => {
    const t = useTour()
    t.start('general', 3)
    t.stop()
    expect(t.active).toBe(false)
    expect(t.seen()).toBe(false)
  })
})

describe('TourOverlay', () => {
  const Page = (html: string) => defineComponent({ render: () => h('div', { innerHTML: html }) })

  async function setup() {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/help', component: Page('<button data-testid="tour-start">start</button>') },
        { path: '/live', component: Page('<nav data-tour="sidebar">nav</nav><span data-testid="live-indicator">Live</span>') },
        { path: '/:any(.*)', component: Page('<p>empty</p>') },
      ],
    })
    await router.push('/help')
    const pinia = createPinia()
    setActivePinia(pinia)
    const Root = defineComponent({ render: () => h('div', [h(TourOverlay), h(RouterView)]) })
    const w = mount(Root, { global: { plugins: [router, pinia] }, attachTo: document.body })
    return { w, router, tour: useTour() }
  }

  const key = (k: string) => window.dispatchEvent(new KeyboardEvent('keydown', { key: k }))

  it('moves to the step screen, shows a Hebrew bubble, follows the arrow keys and closes on Escape', async () => {
    const { w, router, tour } = await setup()
    expect(w.find('[data-testid="tour"]').exists()).toBe(false)
    tour.start()
    await flushPromises()
    const bubble = w.find('[data-testid="tour-bubble"]')
    expect(bubble.attributes('dir')).toBe('rtl')
    expect(bubble.attributes('data-step')).toBe('welcome')
    expect(w.find('[data-testid="tour-count"]').text()).toBe(`שלב 1 מתוך ${TOUR_STEPS.length}`)
    expect(w.find('[data-testid="tour-prev"]').exists()).toBe(false)

    await w.find('[data-testid="tour-next"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/live')
    expect(w.find('[data-testid="tour-bubble"]').attributes('data-step')).toBe('sidebar')
    expect(w.find('[data-testid="tour-bubble"]').html()).toContain('<strong>Sell</strong>')

    key('ArrowLeft') // right to left: forward
    await flushPromises()
    expect(tour.step?.id).toBe('live')
    key('ArrowRight')
    await flushPromises()
    expect(tour.step?.id).toBe('sidebar')

    key('Escape')
    await flushPromises()
    expect(tour.active).toBe(false)
    expect(w.find('[data-testid="tour"]').exists()).toBe(false)
    w.unmount()
  })

  it('covers the page with a click blocker and can be skipped', async () => {
    const { w, tour } = await setup()
    tour.start()
    await flushPromises()
    expect(w.find('.blocker').exists()).toBe(true)
    await w.find('[data-testid="tour-skip"]').trigger('click')
    expect(tour.active).toBe(false)
    w.unmount()
  })
})
