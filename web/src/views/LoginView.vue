<script setup lang="ts">
// Sign in (design screen 0). The session is kept in HttpOnly cookies; nothing is stored in the browser's storage.
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PasswordChangeForm from '@/components/account/PasswordChangeForm.vue'
import Icon from '@/components/ui/Icon.vue'
import { currentLocale, LOCALE_NAMES, switchLocale } from '@/i18n'
import { ApiError, errorText } from '@/lib/api'
import { useAuth } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuth()

const username = ref('admin')
const password = ref('')
const error = ref('')
const busy = ref(false)
// Shown while a temporary password must be replaced. Local state (not auth.mustChangePassword itself): saving the new
// password clears that flag, and the form must stay mounted until it has said it is done.
const choosing = ref(!!auth.user && auth.mustChangePassword)

/** Only same-site paths: never send the user to another origin after logging in. */
function nextPath(): string {
  const next = typeof route.query.next === 'string' ? route.query.next : ''
  return next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/login') ? next : '/today'
}

function message(err: unknown): string {
  if (err instanceof ApiError) {
    // 429 is either the sign-in lockout (wrong passwords) or the gateway's request limit (busy): say which.
    if (err.status === 429 && /^locked out/.test(errorText(err))) return `Too many failed attempts: ${errorText(err).replace(/^locked out; /, '')}.`
    if (err.status === 429) return `The server is busy with this computer's requests: ${errorText(err).replace(/^too many requests; /, '')}.`
    if (err.status === 401) return `Wrong username or password.${errorText(err).replace(/^wrong username or password;?/, '')}`
  }
  return `Could not sign in: ${errorText(err)}`
}

async function passwordChosen() {
  choosing.value = false
  await router.replace(nextPath())
}

async function submit() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const me = await auth.login(username.value.trim(), password.value)
    password.value = ''
    if (me.must_change_password) choosing.value = true
    else await router.replace(nextPath())
  } catch (err) {
    error.value = message(err)
  } finally {
    busy.value = false
  }
}
const otherLang = currentLocale() === 'he' ? 'en' : 'he'
</script>

<template>
  <div class="login">
    <main class="box">
      <div class="brand">
        <div class="logo" aria-hidden="true"><Icon name="brand" :size="18" :stroke-width="2.2" /></div>
        <span class="name">PrintFarm Ops</span>
      </div>
      <section v-if="choosing" class="card" data-testid="password-required">
        <div class="heading">
          <h1>Choose your password</h1>
          <p>Signed in as {{ auth.displayName }}</p>
        </div>
        <PasswordChangeForm required @done="passwordChosen" />
      </section>
      <form v-else class="card" novalidate @submit.prevent="submit">
        <div class="heading">
          <h1>Sign in</h1>
          <p>Local operator console · each person signs in with their own account</p>
        </div>
        <div class="field">
          <label for="user">Username</label>
          <input id="user" v-model="username" class="input" type="text" autocomplete="username" required />
        </div>
        <div class="field">
          <label for="pass">Password</label>
          <input
            id="pass"
            v-model="password"
            class="input"
            :class="{ invalid: error }"
            type="password"
            autocomplete="current-password"
            required
            :aria-invalid="!!error"
            :aria-describedby="error ? 'login-error' : undefined"
          />
          <span v-if="error" id="login-error" role="alert" class="err">{{ error }}</span>
        </div>
        <button type="submit" class="btn btn-primary submit" :disabled="busy || !username || !password">
          {{ busy ? 'Signing in…' : 'Sign in' }}
        </button>
        <p class="note">Session is kept in a secure HttpOnly cookie. This browser keeps only your language choice.</p>
      </form>
      <p class="foot">HTTPS · LAN only ·
        <button type="button" class="linkish" translate="no" data-testid="login-lang" @click="switchLocale(otherLang)">{{ LOCALE_NAMES[otherLang] }}</button>
      </p>
    </main>
  </div>
</template>

<style scoped>
.linkish {
  background: none;
  border: 0;
  padding: 0;
  font: inherit;
  color: var(--accent);
  cursor: pointer;
}
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
}
.box {
  width: 100%;
  max-width: 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: center;
}
.logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--accent);
  color: var(--on-accent);
  display: flex;
  align-items: center;
  justify-content: center;
}
.name {
  font: 700 20px var(--font-display);
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.heading h1 {
  font: 600 18px var(--font);
}
.heading p {
  font-size: 13px;
  color: var(--muted);
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field label {
  font: 500 13px var(--font);
}
.field .input {
  min-height: 44px;
}
.input.invalid {
  border-color: var(--danger);
}
.err {
  font-size: 12px;
  color: var(--danger);
}
.submit {
  min-height: 44px;
  font-weight: 600;
  font-size: 14px;
}
.note {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
}
.foot {
  text-align: center;
  font: 500 12px var(--font-mono);
  color: var(--muted);
}
</style>
