// W2-0: the i18n foundation (catalogue, live re-labelling, missing keys are bugs), the Integrations screen (health,
// secrets set / missing, on/off, test data vs live with a confirmation, Run now, the notifications outbox) and the
// System health card.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import SideNav from '@/components/shell/SideNav.vue'
import IntegrationsHealth from '@/components/system/IntegrationsHealth.vue'
import { i18n, t } from '@/i18n'
import { en } from '@/i18n/en'
import { NAV } from '@/nav'
import { useConfirm } from '@/stores/confirm'
import IntegrationsView from '@/views/IntegrationsView.vue'

import { json, on, serve, signedInAs } from './helpers'

const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })
const CONNECTOR = {
  connector_id: 'demo', kind: 'demo', label: 'Demo store (test data)', enabled: true, mode: 'fake', interval_s: 60,
  secrets_status: { DEMO_STORE_TOKEN: false }, secrets_missing: ['DEMO_STORE_TOKEN'], health: 'ok', last_run_at: '2026-11-20T09:00:00Z',
  last_ok_at: '2026-11-20T09:00:00Z', last_error: null, consecutive_failures: 0, next_run_at: '2026-11-20T09:01:00Z', runs: 3,
  items: 3, mapped: 3, updated_by: 'admin',
}
const NOTES = [{ id: 7, topic: 'safety', severity: 'critical', title: 'x1 did not pause', body: 'Check it now.', link: '/fleet?printer=x1',
  status: 'dashboard', created_at: '2026-11-20T08:00:00Z' }]
const RUNS = [{ id: 3, trigger: 'manual', started_at: '2026-11-20T09:00:00Z', finished_at: '2026-11-20T09:00:01Z', ok: true, items: 3,
  created: 1, updated: 0, error: null }, { id: 2, trigger: 'schedule', started_at: '2026-11-20T08:59:00Z', finished_at: null, ok: false,
  items: 0, created: 0, updated: 0, error: 'timeout' }]

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs('owner')
})
afterEach(() => {
  vi.unstubAllGlobals()
  i18n.global.locale.value = 'en'
})

describe('the i18n foundation', () => {
  it('fills placeholders, and a missing key is a bug, not a silent blank', () => {
    expect(t('integrations.liveBlocked', { keys: 'SHOP_TOKEN' })).toBe('Live mode needs SHOP_TOKEN.')
    expect(() => t('integrations.no_such_key')).toThrow(/no "integrations.no_such_key"/)
  })

  it('every screen of the navigation has a catalogue label, and the sidebar re-labels live (ready for Hebrew)', async () => {
    for (const n of NAV) expect(en.nav[n.id], n.id).toBe(n.label)
    serve()
    const router = anyRoute()
    await router.push('/')
    const w = mount(SideNav, { global: { plugins: [router] } })
    expect(w.text()).toContain('Integrations')
    i18n.global.setLocaleMessage('xx' as never, { ...en, nav: { ...en.nav, integrations: 'שילובים' } } as never)
    i18n.global.locale.value = 'xx' as never
    await flushPromises()
    expect(w.text()).toContain('שילובים')
  })
})

describe('the Integrations screen', () => {
  it('shows health, secrets only as set or missing, recent syncs and the outbox', async () => {
    serve(on('GET', '/api/v1/integrations', () => json(200, { connectors: [CONNECTOR] })),
      on('GET', '/api/v1/notifications', () => json(200, { notifications: NOTES })),
      on('GET', /\/runs\?limit=5$/, () => json(200, { runs: RUNS })))
    const w = mount(IntegrationsView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="health-demo"]').text()).toBe('Healthy')
    expect(w.get('[data-testid="secret-DEMO_STORE_TOKEN"]').text()).toContain('missing')
    expect(w.get('[data-testid="mapped-demo"]').text()).toBe('3')
    expect(w.get('[data-testid="connector-demo"]').text()).toContain('1 new')
    expect(w.get('[data-testid="connector-demo"]').text()).toContain('timeout')
    expect(w.get('[data-testid="note-7"]').text()).toContain('Critical')
    expect(w.get('[data-testid="note-7"]').text()).toContain('x1 did not pause')
  })

  it('switches a connector, confirms the live service first, and runs it now', async () => {
    const calls = serve(on('GET', '/api/v1/integrations', () => json(200, { connectors: [CONNECTOR] })),
      on('GET', '/api/v1/notifications', () => json(200, { notifications: [] })),
      on('GET', /\/runs\?limit=5$/, () => json(200, { runs: [] })),
      on('POST', /\/api\/v1\/commands\/integrations\./, () => json(202, { command_id: 'c' })))
    const ask = vi.spyOn(useConfirm(), 'ask').mockResolvedValue(false)
    const w = mount(IntegrationsView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    await w.get('[data-testid="enable-demo"]').setValue(false)
    await w.get('[data-testid="interval-demo"]').setValue('300')
    await w.get('[data-testid="mode-demo"]').setValue('live')                  // declined: nothing sent
    await w.get('[data-testid="run-demo"]').trigger('click')
    await flushPromises()
    const sent = calls.filter((c) => c.method === 'POST').map((c) => [c.url.split('/').at(-1), c.body])
    expect(sent).toEqual([
      ['integrations.configure', { connector_id: 'demo', enabled: false }],
      ['integrations.configure', { connector_id: 'demo', interval_s: 300 }],
      ['integrations.run', { connector_id: 'demo' }],
    ])
    expect(ask).toHaveBeenCalledWith(expect.objectContaining({ title: 'Switch Demo store (test data) to the live service?' }))
  })
})

describe('the System card', () => {
  it('says how the switched-on connectors are doing', async () => {
    serve(on('GET', '/api/v1/integrations/health', () => json(200, { down: 1, connectors: [
      { ...CONNECTOR, health: 'down', last_error: 'boom' }, { ...CONNECTOR, connector_id: 'x', label: 'Off one', enabled: false, health: 'off' }] })))
    const w = mount(IntegrationsHealth, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.text()).toContain('1 connector(s) failing')
    expect(w.find('[data-testid="health-card-demo"]').text()).toContain('Down')
    expect(w.find('[data-testid="health-card-x"]').exists()).toBe(false)
  })
})
