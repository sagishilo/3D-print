<script setup lang="ts">
// The signed-in person: name and role, change password, sign out. A viewer also sees "Read-only" next to it.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PasswordChangeForm from '@/components/account/PasswordChangeForm.vue'
import Icon from '@/components/ui/Icon.vue'
import { ROLE_HELP, ROLE_LABEL } from '@/lib/roles'
import { useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { useToasts } from '@/stores/toasts'

const auth = useAuth()
const live = useLive()
const router = useRouter()
const toasts = useToasts()

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
  min-width: 260px;
  max-width: calc(100vw - 24px);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.4);
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
  background: rgba(0, 0, 0, 0.55);
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
