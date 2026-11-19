// M2: display preferences (theme, density, Simple / Expert), what Simple mode hides, the plain-words health card,
// mode-aware tours and the shared empty state.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import SideNav from '@/components/shell/SideNav.vue'
import UserMenu from '@/components/shell/UserMenu.vue'
import SimpleHealth from '@/components/system/SimpleHealth.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { TOURS } from '@/lib/page-tours'
import { useAuth } from '@/stores/auth'
import { applyToDocument, defaultMode, usePrefs } from '@/stores/prefs'
import { useSystem } from '@/stores/system'
import { useTour } from '@/stores/tour'
import { useToasts } from '@/stores/toasts'

import { json, on, serve, signedInAs } from './helpers'

const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('applying preferences to the page', () => {
  it('sets the theme and density on <html>, and System leaves the computer in charge', () => {
    const root = document.createElement('html')
    applyToDocument({ theme: 'light', density: 'compact' }, root)
    expect(root.dataset.theme).toBe('light')
    expect(root.dataset.density).toBe('compact')
    applyToDocument({ theme: 'system', density: 'comfortable' }, root)
    expect(root.dataset.theme).toBeUndefined()
    expect(root.dataset.density).toBeUndefined()
  })
})

describe('the prefs store', () => {
  it('starts owners in Expert and everyone else in Simple', () => {
    expect(defaultMode('owner')).toBe('expert')
    expect(defaultMode('operator')).toBe('simple')
    signedInAs('viewer')
    const p = usePrefs()
    expect([p.theme, p.density, p.mode, p.expert]).toEqual(['system', 'comfortable', 'simple', false])
  })

  it('applies a change at once, saves it, and rolls back when the gateway refuses', async () => {
    signedInAs('operator')
    let refuse = false
    const calls = serve(on('PATCH', '/api/v1/auth/prefs', (c) =>
      refuse ? json(422, { detail: 'nope' }) : json(200, { prefs: { ...(c.body as object), saved: true } })))
    const p = usePrefs()
    const pending = p.set({ theme: 'dark' })
    expect(p.theme).toBe('dark')                      // before the answer
    await pending
    expect(calls[0].body).toEqual({ theme: 'dark' })
    expect(useAuth().prefs).toMatchObject({ theme: 'dark', saved: true })
    refuse = true
    await p.set({ mode: 'expert' })
    expect(p.mode).toBe('simple')
    expect(useToasts().items.at(-1)?.title).toBe('Could not save the setting')
  })
})

describe('Simple mode', () => {
  it('hides the agents list in the sidebar; Expert shows it', async () => {
    signedInAs('owner')
    serve()
    useSystem().agents = [{ name: 'fleet', label: 'Fleet Manager', status: 'healthy', tier: 'core' } as never]
    const router = anyRoute()
    await router.push('/')
    const auth = useAuth()
    auth.prefs = { mode: 'simple' }
    let w = mount(SideNav, { global: { plugins: [router] } })
    expect(w.find('[data-testid="nav-agents"]').exists()).toBe(false)
    expect(w.text()).not.toContain('Fleet Manager')
    auth.prefs = { mode: 'expert' }
    w = mount(SideNav, { global: { plugins: [router] } })
    expect(w.find('[data-testid="nav-agents"]').exists()).toBe(true)
    expect(w.text()).toContain('Fleet Manager')
  })

  it('says in plain words what is wrong, or that everything runs', () => {
    signedInAs('operator')
    const system = useSystem()
    system.overview = { postgres: { ok: true }, nats: { ok: true }, go2rtc: { ok: false } } as never
    system.agents = [{ name: 'telemetry', label: 'Telemetry', status: 'down', tier: 'core' } as never]
    let w = mount(SimpleHealth)
    expect(w.text()).toContain('2 things need attention')
    expect(w.text()).toContain('The camera relay does not answer')
    expect(w.text()).toContain('Telemetry is not running')
    system.overview = { postgres: { ok: true }, nats: { ok: true }, go2rtc: { ok: true } } as never
    system.agents = [{ name: 'telemetry', label: 'Telemetry', status: 'healthy', tier: 'core' } as never]
    w = mount(SimpleHealth)
    expect(w.text()).toContain('Everything is running')
  })

  it('tours leave out the steps of the other mode', () => {
    signedInAs('operator')
    const all = TOURS.system.steps
    const t = useTour()
    t.start('system')
    useAuth().prefs = { mode: 'simple' }
    expect(t.steps.map((s) => s.id)).toContain('system-simple-health')
    expect(t.steps.map((s) => s.id)).not.toContain('system-nats')
    useAuth().prefs = { mode: 'expert' }
    expect(t.steps.map((s) => s.id)).toContain('system-nats')
    expect(t.steps.map((s) => s.id)).not.toContain('system-simple-health')
    expect(t.steps.length).toBeLessThan(all.length)
  })
})

describe('user menu display settings', () => {
  it('switches theme, density and mode from the menu', async () => {
    signedInAs('operator')
    const calls = serve(on('PATCH', '/api/v1/auth/prefs', (c) => json(200, { prefs: c.body })))
    const w = mount(UserMenu, { global: { plugins: [anyRoute()] }, attachTo: document.body })
    await w.get('[data-testid="user-menu"]').trigger('click')
    expect(w.get('[data-testid="mode-simple"]').attributes('aria-checked')).toBe('true')
    await w.get('[data-testid="theme-light"]').trigger('click')
    await w.get('[data-testid="density-compact"]').trigger('click')
    await w.get('[data-testid="mode-expert"]').trigger('click')
    await flushPromises()
    expect(calls.map((c) => c.body)).toEqual([{ theme: 'light' }, { density: 'compact' }, { mode: 'expert' }])
    expect(w.get('[data-testid="mode-help"]').text()).toContain('Everything')
    w.unmount()
  })
})

describe('EmptyState', () => {
  it('says what is missing and offers the next step', async () => {
    const w = mount(EmptyState, { props: { title: 'No spools yet', text: 'Register each spool', actionLabel: 'Register spool' } })
    expect(w.text()).toContain('No spools yet')
    await w.get('[data-testid="empty-action"]').trigger('click')
    expect(w.emitted('action')).toHaveLength(1)
    const link = mount(EmptyState, { props: { title: 'No printers yet', actionLabel: 'Connect a printer', to: '/fleet/add' },
      global: { plugins: [anyRoute()] } })
    expect(link.get('[data-testid="empty-action"]').attributes('href')).toBe('/fleet/add')
    const none = mount(EmptyState, { props: { title: 'Nothing here' } })
    expect(none.find('[data-testid="empty-action"]').exists()).toBe(false)
  })
})

describe('quick changes in a row', () => {
  it('an older answer never undoes a newer choice', async () => {
    signedInAs('operator')
    let release: (r: Response) => void = () => undefined
    let first = true
    serve(on('PATCH', '/api/v1/auth/prefs', (c) => {
      if (first) {
        first = false
        return new Promise<Response>((resolve) => (release = resolve)) as unknown as Response
      }
      return json(200, { prefs: { density: (c.body as { density: string }).density } })
    }))
    const p = usePrefs()
    const slow = p.set({ density: 'comfortable' })
    await p.set({ density: 'compact' })
    release(json(200, { prefs: { density: 'comfortable' } }))
    await slow
    expect(p.density).toBe('compact')
  })
})
