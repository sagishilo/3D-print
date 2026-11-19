// Routes: the sign-in page, and the logged-in shell with one route per sidebar entry (lazy-loaded screens).
// Every route except /login needs a session; the guard asks the gateway once (/api/v1/auth/me) and then trusts the
// auth store, which follows refreshes, expiry and logout.
import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'

import { NAV } from '@/nav'
import { needs } from '@/lib/roles'
import { useAuth } from '@/stores/auth'
import { useNiche } from '@/stores/niche'
import { useToasts } from '@/stores/toasts'

const later: RouteRecordRaw[] = NAV.filter((n) => n.phase).map((n) => ({
  path: n.to.slice(1),
  component: () => import('@/views/ComingSoonView.vue'),
  meta: { title: n.label, phase: n.phase, blurb: n.blurb },
}))

export const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true, title: 'Sign in' } },
  {
    path: '/',
    component: () => import('@/components/shell/AppShell.vue'),
    children: [
      { path: '', redirect: '/live' },
      { path: 'live', name: 'live', component: () => import('@/views/LiveView.vue'), meta: { title: 'Live floor', tour: 'live' } },
      { path: 'social', name: 'social', component: () => import('@/views/SocialView.vue'), meta: { title: 'Social & Trends', tour: 'social' } },
      { path: 'catalog', name: 'catalog', component: () => import('@/views/CatalogView.vue'), meta: { title: 'Instant Catalog', tour: 'catalog' } },
      { path: 'niches', name: 'niches', component: () => import('@/views/NichesView.vue'), meta: { title: 'Niches', tour: 'niches', minRole: 'owner' } },
      { path: 'pricing', name: 'pricing', component: () => import('@/views/PricingView.vue'), meta: { title: 'Price calculator', tour: 'pricing', minRole: 'operator' } },
      { path: 'orders', name: 'orders', component: () => import('@/views/OrdersView.vue'), meta: { title: 'Order Pipeline', tour: 'orders' } },
      { path: 'filament', name: 'filament', component: () => import('@/views/FilamentView.vue'), meta: { title: 'Inventory', tour: 'filament' } },
      { path: 'fleet/add', name: 'printer-add', component: () => import('@/views/PrinterOnboardingView.vue'), meta: { title: 'Connect a printer', tour: 'onboarding', minRole: 'operator' } },
      { path: 'fleet', name: 'fleet', component: () => import('@/views/FleetView.vue'), meta: { title: 'Printer Fleet', tour: 'fleet' } },
      { path: 'finance', name: 'finance', component: () => import('@/views/FinanceView.vue'), meta: { title: 'Finance', tour: 'finance', minRole: 'operator' } },
      { path: 'help', name: 'help', component: () => import('@/views/HelpView.vue'), meta: { title: 'מדריך למשתמש', tour: 'help' } },
      { path: 'system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { title: 'System & Agents', tour: 'system', minRole: 'operator' } },
      { path: 'users', name: 'users', component: () => import('@/views/UsersView.vue'), meta: { title: 'Users', tour: 'users', minRole: 'owner' } },
      ...later,
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/live' },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    const auth = useAuth()
    const loggedIn = await auth.check()
    // A temporary password is replaced first (on the sign-in card); the gateway refuses everything else meanwhile.
    if (loggedIn && auth.mustChangePassword) return to.name === 'login' ? true : { name: 'login', query: { next: to.fullPath } }
    if (to.meta.public) return loggedIn && to.name === 'login' ? { path: '/live' } : true
    if (!loggedIn) return { name: 'login', query: to.fullPath === '/live' ? {} : { next: to.fullPath } }
    // A link with ?niche=<slug> opens the screen in that niche (until the person picks another in the top bar).
    if (typeof to.query.niche === 'string') useNiche().linked(to.query.niche)
    if (to.meta.minRole && !auth.can(to.meta.minRole)) {
      useToasts().error(`${to.meta.title ?? 'That screen'} is not available`, `${needs(to.meta.minRole)}.`)
      return { path: '/live' }
    }
    return true
  })

  router.afterEach((to) => {
    document.title = to.meta.title ? `${to.meta.title} · PrintFarm Ops` : 'PrintFarm Ops'
  })

  return router
}
