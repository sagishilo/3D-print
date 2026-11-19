// Users and roles in the dashboard: role rules, the guard, the sidebar, v-can, the user menu, password change,
// the Users screen and the audit log.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, withDirectives } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import PasswordChangeForm from '@/components/account/PasswordChangeForm.vue'
import SideNav from '@/components/shell/SideNav.vue'
import UserMenu from '@/components/shell/UserMenu.vue'
import AuditPanel from '@/components/system/AuditPanel.vue'
import { vCan } from '@/directives/can'
import { auditAction, auditDetail, auditTone } from '@/lib/audit'
import { allows } from '@/lib/roles'
import { createAppRouter } from '@/router'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import LoginView from '@/views/LoginView.vue'
import UsersView from '@/views/UsersView.vue'

import { json, on, serve, signedInAs } from './helpers'

const me = (role: string, must = false) => () =>
  json(200, { user: 'noa', uid: 7, display_name: 'Noa', role, must_change_password: must, prefs: {}, expires_at: Date.now() / 1000 + 900 })

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('roles', () => {
  it('rank viewer < operator < owner', () => {
    expect(allows('owner', 'operator')).toBe(true)
    expect(allows('operator', 'owner')).toBe(false)
    expect(allows('viewer', 'viewer')).toBe(true)
    expect(allows(null, 'viewer')).toBe(false)
  })
})

describe('the guard', () => {
  it('keeps a viewer out of Finance, System and Users', async () => {
    serve(on('GET', '/api/v1/auth/me', me('viewer')))
    const router = createAppRouter(createMemoryHistory())
    for (const path of ['/finance', '/system', '/users']) {
      await router.push(path)
      expect(router.currentRoute.value.path, path).toBe('/live')
    }
    await router.push('/orders')
    expect(router.currentRoute.value.path).toBe('/orders')
  })

  it('lets an operator into Finance but not Users', async () => {
    serve(on('GET', '/api/v1/auth/me', me('operator')))
    const router = createAppRouter(createMemoryHistory())
    await router.push('/finance')
    expect(router.currentRoute.value.path).toBe('/finance')
    await router.push('/users')
    expect(router.currentRoute.value.path).toBe('/live')
  })

  it('sends someone with a temporary password to choose their own first', async () => {
    serve(on('GET', '/api/v1/auth/me', me('operator', true)))
    const router = createAppRouter(createMemoryHistory())
    await router.push('/orders')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.next).toBe('/orders')
  })
})

describe('sidebar', () => {
  async function nav(role: 'owner' | 'operator' | 'viewer') {
    signedInAs(role)
    serve()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })
    await router.push('/')
    return mount(SideNav, { global: { plugins: [router] } }).text()
  }

  it('shows each role only its screens', async () => {
    expect(await nav('owner')).toContain('Users')
    const op = await nav('operator')
    expect(op).toContain('Finance')
    expect(op).not.toContain('Users')
    const viewer = await nav('viewer')
    for (const hidden of ['Finance', 'System & Agents', 'Users']) expect(viewer).not.toContain(hidden)
    expect(viewer).toContain('Order Pipeline')
  })
})

describe('v-can', () => {
  const Btn = defineComponent({ render: () => withDirectives(h('button', { title: 'Pause' }, 'Pause'), [[vCan, 'operator']]) })

  it('disables an action the role cannot do, with the reason', () => {
    signedInAs('viewer')
    const b = mount(Btn).get('button')
    expect((b.element as HTMLButtonElement).disabled).toBe(true)
    expect(b.attributes('title')).toBe('Needs the operator role')
  })

  it('leaves it alone for a role that can', () => {
    signedInAs('operator')
    const b = mount(Btn).get('button')
    expect((b.element as HTMLButtonElement).disabled).toBe(false)
    expect(b.attributes('title')).toBe('Pause')
  })
})

describe('first sign-in', () => {
  it('replaces a temporary password on the sign-in card, then goes where you asked', async () => {
    let must = true
    const me = () => json(200, { user: 'noa', uid: 2, display_name: 'noa', role: 'operator', must_change_password: must, prefs: {}, expires_at: 9e9 })
    serve(
      on('POST', '/api/v1/auth/login', me),
      on('GET', '/api/v1/auth/me', me),
      on('POST', '/api/v1/auth/password', () => { must = false; return me() }),
    )
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/login', name: 'login', component: LoginView }, { path: '/orders', component: { render: () => null } }] })
    await router.push('/login?next=/orders')
    const w = mount(LoginView, { global: { plugins: [router] } })
    await w.get('#user').setValue('noa')
    await w.get('#pass').setValue('temp-temp-temp')
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="password-required"]').exists()).toBe(true)
    await w.get('[data-testid="pw-current"]').setValue('temp-temp-temp')
    await w.get('[data-testid="pw-new"]').setValue('a long and private passphrase')
    await w.get('[data-testid="pw-again"]').setValue('a long and private passphrase')
    await w.get('[data-testid="password-form"]').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/orders')
  })
})

describe('user menu and password', () => {
  it('shows who is signed in, the role, and Read-only for a viewer', async () => {
    signedInAs('viewer', 'dana')
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { render: () => null } }] })
    const w = mount(UserMenu, { global: { plugins: [router] }, attachTo: document.body })
    expect(w.get('[data-testid="read-only"]').text()).toContain('Read-only')
    await w.get('[data-testid="user-menu"]').trigger('click')
    expect(w.get('[data-testid="user-role"]').text()).toBe('Viewer')
    w.unmount()
  })

  it('checks the new password before sending it', async () => {
    signedInAs('operator')
    const calls = serve(on('POST', '/api/v1/auth/password', () => json(200, {
      user: 'admin', uid: 1, display_name: 'admin', role: 'operator', must_change_password: false, prefs: {}, expires_at: 9e9 })))
    const w = mount(PasswordChangeForm, { props: { required: true } })
    await w.get('[data-testid="pw-current"]').setValue('tmp-pass-word-1')
    await w.get('[data-testid="pw-new"]').setValue('short')
    expect(w.get('[data-testid="pw-error"]').text()).toContain('12')
    await w.get('[data-testid="pw-new"]').setValue('a much better passphrase')
    await w.get('[data-testid="pw-again"]').setValue('a much better passphrase!')
    expect(w.get('[data-testid="pw-error"]').text()).toContain('not the same')
    await w.get('[data-testid="pw-again"]').setValue('a much better passphrase')
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(calls[0].body).toEqual({ current_password: 'tmp-pass-word-1', new_password: 'a much better passphrase' })
    expect(w.emitted('done')).toHaveLength(1)
    expect(useAuth().mustChangePassword).toBe(false)
  })
})

describe('Users screen', () => {
  const U = (id: number, username: string, role: string, extra = {}) => ({
    id, username, display_name: username.toUpperCase(), role, must_change_password: false, disabled: false,
    created_at: '2026-11-19T10:00:00Z', created_by: 'admin', last_login_at: null, password_changed_at: null, ...extra })

  it('adds a user and shows the one-time password once; protects you and the last owner', async () => {
    signedInAs('owner')
    useAuth().uid = 1
    const list = [U(1, 'admin', 'owner'), U(2, 'noa', 'operator')]
    const calls = serve(
      on('GET', '/api/v1/users', () => json(200, { users: list })),
      on('POST', '/api/v1/users', () => json(201, { user: U(3, 'dana', 'viewer', { must_change_password: true }), temporary_password: 'abcd-efgh-jkmn-pqrs' })),
      on('PATCH', '/api/v1/users/2', () => json(200, { user: U(2, 'noa', 'viewer') })),
    )
    const w = mount(UsersView, { attachTo: document.body })
    await flushPromises()
    // yourself (the last owner too): no role change, no disable, no reset
    expect(w.get('[data-testid="role-admin"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="toggle-admin"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="reset-admin"]').attributes('disabled')).toBeDefined()

    await w.get('[data-testid="add-user"]').trigger('click')
    await w.get('[data-testid="new-username"]').setValue('dana')
    await w.get('[data-testid="new-role"]').setValue('viewer')
    await w.get('[data-testid="add-user-form"]').trigger('submit')
    await flushPromises()
    expect(calls.find((c) => c.method === 'POST' && c.url === '/api/v1/users')!.body).toEqual({ username: 'dana', display_name: '', role: 'viewer' })
    expect(w.get('[data-testid="temp-password-value"]').text()).toBe('abcd-efgh-jkmn-pqrs')
    await w.get('[data-testid="temp-password-done"]').trigger('click')
    expect(w.find('[data-testid="temp-password"]').exists()).toBe(false)

    // a role change asks first, then patches
    const confirm = useConfirm()
    await w.get('[data-testid="role-noa"]').setValue('viewer')
    await flushPromises()
    expect(confirm.current?.title).toContain('viewer')
    confirm.answer(true)
    await flushPromises()
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ role: 'viewer' })
    w.unmount()
  })
})

describe('audit log', () => {
  it('names actions in plain words', () => {
    expect(auditAction('cmd.coordinator.create_order')).toBe('Orders: create order')
    expect(auditAction('users.reset_password')).toBe('Reset password')
    expect(auditAction('auth.login')).toBe('Sign in')
    expect(auditTone('published')).toBe('ok')
    expect(auditTone('failed: timeout')).toBe('danger')
    expect(auditTone('locked out')).toBe('danger')
    expect(auditDetail({ id: 1, created_at: '', actor: 'a', subject: 's', outcome: 'done',
      payload: { user: 'dana', role: 'viewer', note: 'x' } })).toBe('user: dana · role: viewer')
  })

  it('filters by person and action, and loads older entries', async () => {
    signedInAs('owner')
    const entry = (id: number) => ({ id, created_at: '2026-11-19T10:00:00Z', actor: 'dana', subject: 'users.create', outcome: 'done', payload: { user: 'kim' } })
    const calls = serve(
      on('GET', '/api/v1/system/audit/facets', () => json(200, { actors: ['admin', 'dana'], actions: ['auth.login', 'users.create', 'cmd.coordinator.create_order'] })),
      on('GET', /^\/api\/v1\/system\/audit\?/, (c) =>
        json(200, c.url.includes('before_id=') ? { entries: [entry(1)], next_before_id: null } : { entries: [entry(3), entry(2)], next_before_id: 2 })),
    )
    const w = mount(AuditPanel)
    await flushPromises()
    expect(w.findAll('tbody tr.selectable')).toHaveLength(2)
    expect(w.text()).toContain('Add user')
    await w.get('[data-testid="audit-actor"]').setValue('dana')
    await w.get('[data-testid="audit-action"]').setValue('users')
    await flushPromises()
    const last = calls.filter((c) => c.url.startsWith('/api/v1/system/audit?')).at(-1)!
    expect(last.url).toContain('actor=dana')
    expect(last.url).toContain('action=users')
    await w.get('[data-testid="audit-more"]').trigger('click')
    await flushPromises()
    expect(calls.at(-1)!.url).toContain('before_id=2')
    expect(w.findAll('tbody tr.selectable')).toHaveLength(3)
    // a row opens its full details
    await w.get('[data-testid="audit-3"]').trigger('click')
    expect(w.text()).toContain('"user": "kim"')
  })
})
