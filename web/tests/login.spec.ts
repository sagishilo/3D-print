import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import LoginView from '@/views/LoginView.vue'

import { json, on, serve } from './helpers'

function setup(next?: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginView },
      { path: '/live', component: { template: '<p>live</p>' } },
      { path: '/system', component: { template: '<p>system</p>' } },
    ],
  })
  return { router, start: router.push(next ? { path: '/login', query: { next } } : '/login') }
}

async function signIn(next?: string) {
  const { router, start } = setup(next)
  await start
  const wrapper = mount(LoginView, { global: { plugins: [router] } })
  await wrapper.get('#pass').setValue('secret')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('LoginView', () => {
  it('shows the attempts left after a wrong password', async () => {
    serve(on('POST', '/api/v1/auth/login', () => json(401, { detail: 'wrong username or password; 2 attempts left' })))
    const { wrapper, router } = await signIn()
    expect(wrapper.get('[role=alert]').text()).toBe('Wrong username or password. 2 attempts left')
    expect(wrapper.get('#pass').attributes('aria-invalid')).toBe('true')
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('explains a lockout', async () => {
    serve(on('POST', '/api/v1/auth/login', () => json(429, { detail: 'locked out; retry in 840 s' })))
    const { wrapper } = await signIn()
    expect(wrapper.get('[role=alert]').text()).toBe('Too many failed attempts: retry in 840 s.')
  })

  it('tells a busy server apart from a lockout', async () => {
    serve(on('POST', '/api/v1/auth/login', () => json(429, { detail: 'too many requests; retry in 3 s' })))
    const { wrapper } = await signIn()
    expect(wrapper.get('[role=alert]').text()).toBe("The server is busy with this computer's requests: retry in 3 s.")
  })

  it('goes back to the screen it came from after signing in', async () => {
    const calls = serve(
      on('POST', '/api/v1/auth/login', () => json(200, { ok: true })),
      on('GET', '/api/v1/auth/me', () => json(200, { user: 'admin', expires_at: Date.now() / 1000 + 900 })),
    )
    const { router } = await signIn('/system')
    expect(router.currentRoute.value.path).toBe('/system')
    expect(calls[0].body).toEqual({ username: 'admin', password: 'secret' })
  })

  it('never redirects to another site', async () => {
    serve(
      on('POST', '/api/v1/auth/login', () => json(200, {})),
      on('GET', '/api/v1/auth/me', () => json(200, { user: 'admin', expires_at: Date.now() / 1000 + 900 })),
    )
    const { router } = await signIn('//evil.example/phish')
    expect(router.currentRoute.value.path).toBe('/live')
  })
})
