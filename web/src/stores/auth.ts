// Session state. The session itself lives in HttpOnly cookies (15-min access token, 7-day rotating refresh token);
// this store only knows who is logged in, their role, and refreshes the access token a minute before it expires.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, onUnauthorized, refreshSession } from '@/lib/api'
import { allows, type Role } from '@/lib/roles'

export interface Me {
  user: string
  uid: number
  display_name: string
  role: Role
  must_change_password: boolean
  prefs: Record<string, unknown>
  expires_at: number // unix seconds
}

export const useAuth = defineStore('auth', () => {
  const user = ref<string | null>(null)
  const uid = ref<number | null>(null)
  const displayName = ref('')
  const role = ref<Role | null>(null)
  const mustChangePassword = ref(false)
  const expiresAt = ref(0)
  const checked = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let checking: Promise<boolean> | null = null

  function schedule() {
    if (timer) clearTimeout(timer)
    const ms = Math.max(expiresAt.value - Date.now() - 60_000, 5_000)
    timer = setTimeout(async () => {
      if (await refreshSession()) await check(true)
      else expired()
    }, ms)
  }

  /** Who is logged in (asks the gateway once; ``force`` asks again). */
  function check(force = false): Promise<boolean> {
    if (checked.value && !force) return Promise.resolve(user.value !== null)
    checking ??= api<Me>('/api/v1/auth/me')
      .then((me) => {
        apply(me)
        schedule()
        return true
      })
      .catch(() => {
        user.value = null
        return false
      })
      .finally(() => {
        checked.value = true
        checking = null
      })
    return checking
  }

  function apply(me: Me) {
    user.value = me.user
    uid.value = me.uid
    displayName.value = me.display_name || me.user
    role.value = me.role
    mustChangePassword.value = me.must_change_password
    expiresAt.value = me.expires_at * 1000
  }

  async function login(username: string, password: string): Promise<Me> {
    await api('/api/v1/auth/login', { method: 'POST', body: { username, password } })
    await check(true)
    return { user: user.value!, uid: uid.value!, display_name: displayName.value, role: role.value!,
      must_change_password: mustChangePassword.value, prefs: {}, expires_at: expiresAt.value / 1000 }
  }

  /** Change your own password (required after an owner set a temporary one); the gateway starts a fresh session. */
  async function changePassword(current: string, next: string): Promise<void> {
    const me = await api<Me>('/api/v1/auth/password', { method: 'POST', body: { current_password: current, new_password: next } })
    apply(me)
    schedule()
  }

  /** At least this role (the gateway checks it too). */
  const can = (min: Role) => allows(role.value, min)
  const isViewer = computed(() => role.value === 'viewer')

  async function logout(): Promise<void> {
    try {
      await api('/api/v1/auth/logout', { method: 'POST' })
    } finally {
      expired()
    }
  }

  function expired() {
    if (timer) clearTimeout(timer)
    timer = null
    user.value = null
    uid.value = null
    role.value = null
    mustChangePassword.value = false
    checked.value = true
  }

  onUnauthorized(expired)

  return { user, uid, displayName, role, mustChangePassword, expiresAt, checked, isViewer, check, login, changePassword,
    can, logout, expired }
})
