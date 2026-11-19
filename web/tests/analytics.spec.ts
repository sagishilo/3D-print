// M6: the niche scorecard (niches side by side, suggestions, downloads, rebuild) and the Trends panel on Social.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import TrendPanel from '@/components/social/TrendPanel.vue'
import DownloadMenu from '@/components/ui/DownloadMenu.vue'
import { demandLabel, download, type NicheScore, type Scorecard } from '@/lib/analytics'
import { useConfirm } from '@/stores/confirm'
import ScorecardView from '@/views/ScorecardView.vue'

import { json, on, serve, signedInAs } from './helpers'

const score = (over: Partial<NicheScore> = {}): NicheScore => ({
  niche: 'home', name: 'Home décor', color: 1, target_margin_pct: 50, revenue_agorot: 50000, cost_agorot: 10000,
  profit_agorot: 40000, margin_pct: 80, revenue_prev_agorot: 30000, profit_prev_agorot: 22000, units: 20, units_prev: 10,
  demand_pct: 100, print_hours: 10, profit_per_print_hour_agorot: 4000, qc_checked: 20, qc_failed: 3, qc_fail_pct: 15,
  leads: 4, leads_prev: 1, direction: 'rising', rising: ['HOOK'], falling: [], rising_detail: [], below_target: 0,
  insight: 'עלייה יפה השבוע', suggestions: [
    { kind: 'batch', sku: 'HOOK', quantity: 9, text: 'HOOK is rising: queue 9 for stock', to: '/catalog?niche=home' },
    { kind: 'quality', text: '15% of checked pieces failed QC', to: '/niches' }],
  ...over,
})
const CARD: Scorecard = {
  days: 30, end: '2026-11-19', revenue_agorot: 60000, signals_day: '2026-11-18', rolled_at: null,
  niches: [score(), score({ niche: 'kids', name: 'Kids & toys', color: 2, revenue_agorot: 10000, profit_agorot: 2000,
    profit_per_print_hour_agorot: 500, direction: null, rising: [], insight: null, suggestions: [] })],
}
const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })

beforeEach(() => {
  setActivePinia(createPinia())
  signedInAs('owner')
})
afterEach(() => vi.unstubAllGlobals())

describe('analytics helpers', () => {
  it('builds download links and demand labels', () => {
    expect(download('/api/v1/analytics/scorecard?days=30', 'xlsx')).toBe('/api/v1/analytics/scorecard?days=30&format=xlsx')
    expect(download('/api/v1/finance/unit-economics', 'csv')).toBe('/api/v1/finance/unit-economics?format=csv')
    expect(demandLabel(60, 8)).toBe('▲ 60%')
    expect(demandLabel(-20, 4)).toBe('▼ 20%')
    expect(demandLabel(null, 3)).toBe('new')
    expect(demandLabel(null, 0)).toBe('—')
    const w = mount(DownloadMenu, { props: { path: '/x?a=1', testid: 'd' } })
    expect(w.get('[data-testid="d-csv"]').attributes('href')).toBe('/x?a=1&format=csv')
  })
})

describe('niche scorecard', () => {
  it('shows niches side by side, marks the best per print hour and queues a suggested batch', async () => {
    const calls = serve(on('GET', /\/api\/v1\/analytics\/scorecard/, () => json(200, CARD)),
      on('POST', '/api/v1/commands/catalog.batch', () => json(202, { command_id: 'c1' })),
      on('POST', '/api/v1/commands/analytics.backfill', () => json(202, { command_id: 'c2' })))
    vi.spyOn(useConfirm(), 'ask').mockResolvedValue(true)
    const w = mount(ScorecardView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="sc-revenue-home"]').text()).toBe('₪500.00')
    expect(w.get('[data-testid="sc-dir-home"]').text()).toContain('rising')
    expect(w.get('[data-testid="sc-home"]').text()).toContain('best per hour')
    expect(w.get('[data-testid="sc-insight-home"]').text()).toBe('עלייה יפה השבוע')
    expect(w.get('[data-testid="sc-download-xlsx"]').attributes('href')).toBe('/api/v1/analytics/scorecard?days=30&format=xlsx')
    await w.get('[data-testid="sc-act-HOOK"]').trigger('click')
    await flushPromises()
    expect(calls.find((c) => c.url.endsWith('catalog.batch'))?.body).toMatchObject({ items: [{ product: 'catalog', sku: 'HOOK', quantity: 9 }] })
    await w.get('[data-testid="sc-rebuild"]').trigger('click')
    await flushPromises()
    expect(calls.find((c) => c.url.endsWith('analytics.backfill'))?.body).toEqual({ days: 120 })
    await w.get('[data-testid="sc-days"]').setValue('90')
    await flushPromises()
    expect(calls.at(-1)?.url).toBe('/api/v1/analytics/scorecard?days=90')
  })

  it('offers the rebuild to owners only', async () => {
    signedInAs('operator')
    serve(on('GET', /\/api\/v1\/analytics\/scorecard/, () => json(200, CARD)))
    const w = mount(ScorecardView, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="sc-rebuild"]').attributes('disabled')).toBeDefined()
  })
})

describe('trends on the social page', () => {
  it('lists what is rising and cooling with the reason, and the insight', async () => {
    serve(on('GET', /\/api\/v1\/trend\/signals/, () => json(200, {
      day: '2026-11-18', insights: [{ niche: 'home', text: 'תובנה', day: '2026-11-18' }],
      signals: [{ scope: 'sku', subject: 'HOOK', niche: 'home', direction: 'rising', strength: 1, change_pct: 160,
        evidence: ['13 sold in the last 7 days (5 the 7 days before)'], label: 'Hook' }],
    })))
    const w = mount(TrendPanel, { global: { plugins: [anyRoute()] } })
    await flushPromises()
    expect(w.get('[data-testid="signal-HOOK"]').text()).toContain('13 sold in the last 7 days')
    expect(w.text()).toContain('תובנה')
    expect(w.text()).toContain('Open the niche scorecard')
  })
})
