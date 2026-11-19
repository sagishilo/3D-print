<script setup lang="ts">
// The signed-in person: name and role, display settings (theme, density, Simple / Expert), change password, sign out.
// A viewer also sees "Read-only" next to it. Display settings are saved per person (stores/prefs.ts).
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PasswordChangeForm from '@/components/account/PasswordChangeForm.vue'
import Icon from '@/components/ui/Icon.vue'
import { ROLE_HELP, ROLE_LABEL } from '@/lib/roles'
import { useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { type Density, type Mode, type Theme, usePrefs } from '@/stores/prefs'
import { useToasts } from '@/stores/toasts'

const auth = useAuth()
const live = useLive()
const router = useRouter()
const toasts = useToasts()
const prefs = usePrefs()

const THEMES: { id: Theme; label: string }[] = [
  { id: 'system', label: 'System' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
]
const DENSITIES: { id: Density; label: string }[] = [
  { id: 'comfortable', label: 'Comfortable' },
  { id: 'compact', label: 'Compact' },
]
const MODES: { id: Mode; label: string; help: string }[] = [
  { id: 'simple', label: 'Simple', help: 'Daily work: technical panels (NATS, dead letters, agents, advanced settings) are hidden' },
  { id: 'expert', label: 'Expert', help: 'Everything, including the technical panels of System & Agents' },
]

const open = ref(false)
const changing = ref(false)
const root = ref<HTMLElement | null>(null)

function onDoc(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    open.value = false
    changing.value = false
  }
}
onMounted(() => {
  document.addEventListener('mousedown', onDoc)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDoc)
  document.removeEventListener('keydown', onKey)
})

async function logout() {
  open.value = false
  live.stop()
  await auth.logout()
  void router.push({ name: 'login' })
}

function changed() {
  changing.value = false
  toasts.ok('Password changed', 'Your other sessions were signed out.')
}
</script>

<template>
  <div ref="root" class="user-menu">
    <span v-if="auth.isViewer" class="chip readonly" data-testid="read-only" title="Your role can look, not change">
      <Icon name="lock" :size="12" />Read-only
    </span>
    <button
      type="button"
      class="btn user"
      :aria-expanded="open"
      aria-haspopup="menu"
      :aria-label="`Account of ${auth.displayName}: ${auth.role ? ROLE_LABEL[auth.role] : ''}`"
      data-testid="user-menu"
      @click="open = !open"
    >
      <span class="avatar" aria-hidden="true">{{ (auth.displayName || '?')[0].toUpperCase() }}</span>
      <span class="wide">{{ auth.displayName }}</span>
    </button>
    <div v-if="open" class="menu" role="menu" data-testid="user-menu-panel">
      <div class="who">
        <b>{{ auth.displayName }}</b>
        <span class="muted small">{{ auth.user }}</span>
        <span v-if="auth.role" class="chip role" :class="auth.role" data-testid="user-role">{{ ROLE_LABEL[auth.role] }}</span>
        <span v-if="auth.role" class="muted small">{{ ROLE_HELP[auth.role] }}</span>
      </div>
      <div class="prefs" data-testid="display-prefs">
        <div class="pref">
          <span class="label">Theme</span>
          <div class="seg" role="radiogroup" aria-label="Theme">
            <button v-for="t in THEMES" :key="t.id" type="button" role="radio" :aria-checked="prefs.theme === t.id"
              :class="{ on: prefs.theme === t.id }" :data-testid="`theme-${t.id}`" @click="prefs.set({ theme: t.id })">{{ t.label }}</button>
          </div>
        </div>
        <div class="pref">
          <span class="label">Density</span>
          <div class="seg" role="radiogroup" aria-label="Density">
            <button v-for="d in DENSITIES" :key="d.id" type="button" role="radio" :aria-checked="prefs.density === d.id"
              :class="{ on: prefs.density === d.id }" :data-testid="`density-${d.id}`" @click="prefs.set({ density: d.id })">{{ d.label }}</button>
          </div>
        </div>
        <div class="pref">
          <span class="label">Mode</span>
          <div class="seg" role="radiogroup" aria-label="Simple or Expert mode">
            <button v-for="m in MODES" :key="m.id" type="button" role="radio" :aria-checked="prefs.mode === m.id" :title="m.help"
              :class="{ on: prefs.mode === m.id }" :data-testid="`mode-${m.id}`" @click="prefs.set({ mode: m.id })">{{ m.label }}</button>
          </div>
          <span class="muted small" data-testid="mode-help">{{ MODES.find((m) => m.id === prefs.mode)?.help }}</span>
        </div>
      </div>
      <button type="button" role="menuitem" class="item" data-testid="change-password" @click="(open = false), (changing = true)">
        <Icon name="lock" :size="14" />Change password
      </button>
      <button type="button" role="menuitem" class="item" data-testid="logout" @click="logout">
        <Icon name="logout" :size="14" />Log out
      </button>
    </div>
    <div v-if="changing" class="scrim" @click.self="changing = false">
      <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="pw-title">
        <h2 id="pw-title">Change password</h2>
        <PasswordChangeForm @done="changed" @cancel="changing = false" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.user-menu {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
}
.user {
  background: var(--surface);
}
.avatar {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--surface-2);
  font: 600 11px var(--font);
  display: flex;
  align-items: center;
  justify-content: center;
}
.readonly {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--warn);
  border-color: var(--warn);
}
.menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 300px;
  max-width: calc(100vw - 24px);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  padding: 6px;
  z-index: 40;
}
.who {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}
.role {
  align-self: flex-start;
}
.role.owner {
  color: var(--accent);
  border-color: var(--accent);
}
.small {
  font-size: 12px;
}
.prefs {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}
.pref {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pref .label {
  font: 600 11px var(--font);
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.seg {
  display: flex;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.seg button {
  flex: 1;
  min-height: 30px;
  padding: 0 8px;
  background: var(--surface);
  border: 0;
  border-inline-start: 1px solid var(--border);
  color: var(--muted);
  font: 500 12px var(--font);
  cursor: pointer;
}
.seg button:first-child {
  border-inline-start: 0;
}
.seg button.on {
  background: var(--accent-bg);
  color: var(--accent);
  font-weight: 600;
}
.item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: none;
  border: 0;
  border-radius: 6px;
  color: var(--text);
  font: 500 13px var(--font);
  cursor: pointer;
  text-align: left;
}
.item:hover,
.item:focus-visible {
  background: var(--surface-2);
}
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
  padding: 16px;
}
.dialog {
  width: min(420px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 22px;
}
.dialog h2 {
  font: 700 18px var(--font-display);
  margin: 0 0 14px;
}
@media (max-width: 600px) {
  .wide,
  .readonly {
    display: none;
  }
  .user {
    padding: 0 7px;
  }
}
</style>
