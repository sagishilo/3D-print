// Session state. The session itself lives in HttpOnly cookies (15-min access token, 7-day rotating refresh token);
// this store only knows who is logged in and refreshes the access token a minute before it expires.
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, onUnauthorized, refreshSession } from '@/lib/api'

interface Me {
  user: string
  expires_at: number // unix seconds
}

export const useAuth = defineStore('auth', () => {
  const user = ref<string | null>(null)
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
        user.value = me.user
        expiresAt.value = me.expires_at * 1000
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

  async function login(username: string, password: string): Promise<void> {
    await api('/api/v1/auth/login', { method: 'POST', body: { username, password } })
    await check(true)
  }

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
    checked.value = true
  }

  onUnauthorized(expired)

  return { user, expiresAt, checked, check, login, logout, expired }
})
