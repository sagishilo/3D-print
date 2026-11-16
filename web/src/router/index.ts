// Routes: the sign-in page, and the logged-in shell with one route per sidebar entry (lazy-loaded screens).
// Every route except /login needs a session; the guard asks the gateway once (/api/v1/auth/me) and then trusts the
// auth store, which follows refreshes, expiry and logout.
import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'

import { NAV } from '@/nav'
import { useAuth } from '@/stores/auth'

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
      { path: 'orders', name: 'orders', component: () => import('@/views/OrdersView.vue'), meta: { title: 'Order Pipeline', tour: 'orders' } },
      { path: 'filament', name: 'filament', component: () => import('@/views/FilamentView.vue'), meta: { title: 'Filament', tour: 'filament' } },
      { path: 'fleet', name: 'fleet', component: () => import('@/views/FleetView.vue'), meta: { title: 'Printer Fleet', tour: 'fleet' } },
      { path: 'finance', name: 'finance', component: () => import('@/views/FinanceView.vue'), meta: { title: 'Finance', tour: 'finance' } },
      { path: 'help', name: 'help', component: () => import('@/views/HelpView.vue'), meta: { title: 'מדריך למשתמש', tour: 'help' } },
      { path: 'system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { title: 'System & Agents', tour: 'system' } },
      ...later,
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/live' },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    const loggedIn = await useAuth().check()
    if (to.meta.public) return loggedIn && to.name === 'login' ? { path: '/live' } : true
    if (loggedIn) return true
    return { name: 'login', query: to.fullPath === '/live' ? {} : { next: to.fullPath } }
  })

  router.afterEach((to) => {
    document.title = to.meta.title ? `${to.meta.title} · PrintFarm Ops` : 'PrintFarm Ops'
  })

  return router
}
