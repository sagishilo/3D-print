// M3: niches. The helpers, the niche store (saved per person, a ?niche= link wins for the visit), the top-bar
// switcher, the "Filtered" bar, stores asking for one niche, bulk assign and who sees the Niches screen.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import AssignNicheDialog from '@/components/catalog/AssignNicheDialog.vue'
import NicheSwitcher from '@/components/shell/NicheSwitcher.vue'
import SideNav from '@/components/shell/SideNav.vue'
import NicheFilterBar from '@/components/ui/NicheFilterBar.vue'
import type { CatalogProduct } from '@/lib/catalog'
import { commaList, lines, type Niche, slugError, slugFromName, withNiche } from '@/lib/niches'
import { useAuth } from '@/stores/auth'
import { useCatalog } from '@/stores/catalog'
import { useNiche } from '@/stores/niche'
import { useOrders } from '@/stores/orders'

import { json, on, serve, signedInAs } from './helpers'

const niche = (slug: string, over: Partial<Niche> = {}): Niche => ({
  slug, name: slug === 'kids' ? 'Kids & toys' : slug === 'home' ? 'Home décor' : slug, color: slug === 'kids' ? 2 : 1,
  icon: 'tag', sort: 10, archived: false, default_material: 'PLA', default_colors: ['white'], target_margin_pct: 50,
  qc_checklist: [], packaging: [], products: 3, tagged: 0, ...over,
})
const NICHES = [niche('home'), niche('kids'), niche('old', { archived: true }), niche('unassigned', { color: 8 })]
const niches = on('GET', '/api/v1/niches?include_archived=true', () => json(200, { niches: NICHES }))
const prefs = on('PATCH', '/api/v1/auth/prefs', (c) => json(200, { prefs: { ...(useAuth().prefs as object), ...(c.body as object) } }))
const anyRoute = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  signedInAs('owner')
})
afterEach(() => vi.unstubAllGlobals())

describe('niche helpers', () => {
  it('makes slugs, lists and niche-scoped URLs', () => {
    expect(slugFromName('Kids & toys')).toBe('kids-toys')
    expect(slugFromName('  Home décor ')).toBe('home-decor')
    expect(slugError('kids')).toBeNull()
    expect(slugError('Kids')).not.toBeNull()
    expect(lines('a\n\n b \n')).toEqual(['a', 'b'])
    expect(commaList('white, black,,')).toEqual(['white', 'black'])
    expect(withNiche('/api/v1/orders', null)).toBe('/api/v1/orders')
    expect(withNiche('/api/v1/orders', 'kids')).toBe('/api/v1/orders?niche=kids')
    expect(withNiche('/api/v1/x?a=1', 'kids')).toBe('/api/v1/x?a=1&niche=kids')
  })
})

describe('the niche store', () => {
  it('follows the saved choice, a link wins for the visit, and picking saves it per person', async () => {
    const calls = serve(niches, prefs)
    const n = useNiche()
    await n.load()
    expect(n.list.map((x) => x.slug)).toEqual(['home', 'kids', 'unassigned'])     // archived ones are not offered
    expect(n.current).toBeNull()
    useAuth().prefs = { niche: 'home' }
    expect(n.current).toBe('home')
    expect(n.active?.name).toBe('Home décor')
    n.linked('kids')
    expect(n.current).toBe('kids')
    await n.set(null)
    expect(calls.at(-1)?.body).toEqual({ niche: 'all' })
    expect(n.current).toBeNull()
    expect(n.name(null)).toBe('All niches')
  })

  it('stores ask the gateway for the chosen niche and reload when it changes', async () => {
    const calls = serve(niches, prefs, on('GET', /\/api\/v1\//, () => json(200, { orders: [], plates: [], products: [], categories: [], counts: {} })))
    useAuth().prefs = { niche: 'kids' }
    await useOrders().load()
    await useCatalog().load()
    expect(calls.map((c) => c.url)).toContain('/api/v1/orders?niche=kids')
    expect(calls.some((c) => c.url.startsWith('/api/v1/catalog/products?') && c.url.includes('niche=kids'))).toBe(true)
    await useNiche().set(null)
    await flushPromises()
    expect(calls.filter((c) => c.url === '/api/v1/orders').length).toBe(1)      // reloaded without the filter
  })
})

describe('the top-bar switcher and the filtered bar', () => {
  it('lists the active niches and saves the pick', async () => {
    const calls = serve(niches, prefs)
    const w = mount(NicheSwitcher, { attachTo: document.body })
    await flushPromises()
    expect(w.get('[data-testid="niche-switcher"]').text()).toContain('All niches')
    await w.get('[data-testid="niche-switcher"]').trigger('click')
    expect(w.find('[data-testid="niche-option-old"]').exists()).toBe(false)
    await w.get('[data-testid="niche-option-kids"]').trigger('click')
    await flushPromises()
    expect(calls.at(-1)).toMatchObject({ method: 'PATCH', body: { niche: 'kids' } })
    expect(w.get('[data-testid="niche-switcher"]').text()).toContain('Kids & toys')
    w.unmount()
  })

  it('says a screen is filtered and shows everything again', async () => {
    const calls = serve(niches, prefs)
    await useNiche().load()
    const w = mount(NicheFilterBar, { props: { what: 'orders' } })
    expect(w.find('[data-testid="niche-filter-bar"]').exists()).toBe(false)
    useAuth().prefs = { niche: 'kids' }
    await flushPromises()
    expect(w.text()).toContain('Filtered: Kids & toys')
    await w.get('[data-testid="niche-show-all"]').trigger('click')
    await flushPromises()
    expect(calls.at(-1)?.body).toEqual({ niche: 'all' })
    expect(w.find('[data-testid="niche-filter-bar"]').exists()).toBe(false)
  })
})

describe('assign niche (bulk)', () => {
  it('moves the ticked products to one niche', async () => {
    const calls = serve(niches, on('POST', '/api/v1/commands/catalog.niche.assign', () => json(202, { command_id: 'c1' })),
      on('GET', /\/api\/v1\/catalog/, () => json(200, { products: [], categories: [], counts: {} })))
    await useNiche().load()
    const products = ['A-1', 'B-2'].map((sku) => ({ sku, name: sku, niche: 'unassigned', tags: [] }) as unknown as CatalogProduct)
    const w = mount(AssignNicheDialog, { props: { products } })
    await w.get('[data-testid="assign-target"]').setValue('kids')
    await w.get('[data-testid="assign-all"]').setValue(true)
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ skus: ['A-1', 'B-2'], niche: 'kids' })
    expect(w.emitted('close')).toHaveLength(1)
  })
})

describe('the Niches screen', () => {
  it('is in the sidebar for owners only', async () => {
    serve()
    const router = anyRoute()
    await router.push('/')
    let w = mount(SideNav, { global: { plugins: [router] } })
    expect(w.text()).toContain('Niches')
    signedInAs('operator')
    w = mount(SideNav, { global: { plugins: [router] } })
    expect(w.text()).not.toContain('Niches')
  })
})
