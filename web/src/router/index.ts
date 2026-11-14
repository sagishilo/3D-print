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
      { path: 'live', name: 'live', component: () => import('@/views/LiveView.vue'), meta: { title: 'Live floor' } },
      { path: 'system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { title: 'System & Agents' } },
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
