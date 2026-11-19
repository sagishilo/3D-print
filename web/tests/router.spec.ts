import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { createAppRouter, routes } from '@/router'

import { json, on, serve } from './helpers'

const me = () => json(200, { user: 'admin', expires_at: Date.now() / 1000 + 900 })

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('route table', () => {
  it('has a lazy-loaded screen for every sidebar entry', () => {
    const shell = routes.find((r) => r.path === '/')!
    const paths = shell.children!.map((c) => c.path)
    expect(paths).toEqual(expect.arrayContaining(['live', 'system', 'social', 'catalog', 'orders', 'fleet', 'filament', 'finance']))
    const social = shell.children!.find((c) => c.path === 'social')!
    expect(social.meta).toMatchObject({ title: 'Social & Trends' })
    expect(social.meta?.phase).toBeUndefined()                       // a real screen since phase 10
    expect(typeof social.component).toBe('function')
  })
})

describe('auth guard', () => {
  it('sends a visitor without a session to sign in and keeps the screen to return to', async () => {
    serve(
      on('GET', '/api/v1/auth/me', () => json(401, { detail: 'not authenticated' })),
      on('POST', '/api/v1/auth/refresh', () => json(401, { detail: 'no refresh token' })),
    )
    const router = createAppRouter(createMemoryHistory())
    await router.push('/system?tab=dlq')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.next).toBe('/system?tab=dlq')
  })

  it('lets a logged-in user in and keeps them away from the sign-in page', async () => {
    const calls = serve(on('GET', '/api/v1/auth/me', me))
    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    expect(router.currentRoute.value.path).toBe('/today')
    expect(document.title).toBe('Today · PrintFarm Ops')
    await router.push('/social')
    expect(router.currentRoute.value.name).toBe('social')
    expect(calls.filter((c) => c.url === '/api/v1/auth/me')).toHaveLength(1) // asked once, then trusted
  })
})
