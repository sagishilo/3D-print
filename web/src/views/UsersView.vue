<script setup lang="ts">
// Users (owner only): who can sign in, with which role. New accounts and password resets get a one-time temporary
// password that is shown here once; the person replaces it at their first sign-in.
import { computed, onMounted, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { api, errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import { ROLE_HELP, ROLE_LABEL, type Role } from '@/lib/roles'
import { copyText } from '@/lib/social'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { useToasts } from '@/stores/toasts'

interface User {
  id: number
  username: string
  display_name: string
  role: Role
  must_change_password: boolean
  disabled: boolean
  created_at: string
  created_by: string
  last_login_at: string | null
  password_changed_at: string | null
}

const auth = useAuth()
const confirm = useConfirm()
const toasts = useToasts()

const users = ref<User[]>([])
const loaded = ref(false)
const error = ref('')
const busy = ref<number | 'new' | null>(null)
const adding = ref(false)
const form = ref({ username: '', display_name: '', role: 'operator' as Role })
const fresh = ref<{ username: string; password: string; why: string } | null>(null)

const ROLES: Role[] = ['owner', 'operator', 'viewer']
const counts = computed(() => ({
  active: users.value.filter((u) => !u.disabled).length,
  owners: users.value.filter((u) => u.role === 'owner' && !u.disabled).length,
}))
const usernameOk = computed(() => /^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$/.test(form.value.username))

async function load() {
  try {
    users.value = (await api<{ users: User[] }>('/api/v1/users')).users
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loaded.value = true
  }
}
onMounted(load)

async function create() {
  if (!usernameOk.value || busy.value) return
  busy.value = 'new'
  try {
    const r = await api<{ user: User; temporary_password: string }>('/api/v1/users', { method: 'POST', body: form.value })
    fresh.value = { username: r.user.username, password: r.temporary_password, why: 'new account' }
    adding.value = false
    form.value = { username: '', display_name: '', role: 'operator' }
    await load()
  } catch (err) {
    toasts.error('Could not add the user', errorText(err))
  } finally {
    busy.value = null
  }
}

async function change(u: User, body: Partial<Pick<User, 'role' | 'disabled' | 'display_name'>>, what: string) {
  busy.value = u.id
  try {
    await api(`/api/v1/users/${u.id}`, { method: 'PATCH', body })
    toasts.ok(what, body.role || body.disabled !== undefined ? `${u.username} is signed out everywhere; the change applies at once.` : '')
    await load()
  } catch (err) {
    toasts.error(`Could not change ${u.username}`, errorText(err))
    await load()
  } finally {
    busy.value = null
  }
}

async function setRole(u: User, role: Role) {
  if (role === u.role) return
  const ok = await confirm.ask({
    title: `Make ${u.display_name || u.username} ${ROLE_LABEL[role].toLowerCase()}?`,
    text: `${ROLE_LABEL[role]}: ${ROLE_HELP[role]}. They are signed out and sign in again with the new role.`,
    confirmLabel: `Make ${ROLE_LABEL[role].toLowerCase()}`,
  })
  if (ok) await change(u, { role }, `Role changed to ${ROLE_LABEL[role].toLowerCase()}`)
  else await load()
}

async function toggleDisabled(u: User) {
  if (!u.disabled) {
    const ok = await confirm.ask({
      title: `Disable ${u.display_name || u.username}?`,
      text: 'They are signed out now and cannot sign in until an owner enables the account again. Their history stays.',
      confirmLabel: 'Disable',
      danger: true,
    })
    if (!ok) return
  }
  await change(u, { disabled: !u.disabled }, u.disabled ? 'Account enabled' : 'Account disabled')
}

async function resetPassword(u: User) {
  const ok = await confirm.ask({
    title: `Reset the password of ${u.display_name || u.username}?`,
    text: 'You get a one-time temporary password to hand over. Their sessions end now; at the next sign-in they choose their own password.',
    confirmLabel: 'Reset password',
  })
  if (!ok) return
  busy.value = u.id
  try {
    const r = await api<{ temporary_password: string }>(`/api/v1/users/${u.id}/reset-password`, { method: 'POST' })
    fresh.value = { username: u.username, password: r.temporary_password, why: 'password reset' }
    await load()
  } catch (err) {
    toasts.error('Could not reset the password', errorText(err))
  } finally {
    busy.value = null
  }
}

async function copyFresh() {
  if (fresh.value && (await copyText(fresh.value.password))) toasts.ok('Temporary password copied', '')
}

const isMe = (u: User) => u.id === auth.uid
const lastOwner = (u: User) => u.role === 'owner' && !u.disabled && counts.value.owners <= 1
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Users</h1>
      <p>{{ counts.active }} active · each person signs in with their own account · every change is in the audit log</p>
    </div>
    <div class="head-actions">
      <button type="button" class="btn btn-primary" data-testid="add-user" @click="adding = !adding"><Icon name="plus" :size="14" />Add user</button>
    </div>
  </div>

  <section v-if="fresh" class="panel fresh" role="status" data-testid="temp-password">
    <div>
      <b>One-time password for {{ fresh.username }}</b> ({{ fresh.why }})
      <p class="muted small">Shown only now. Hand it over in person or by message; they choose their own password at the first sign-in.</p>
    </div>
    <code class="pw" data-testid="temp-password-value">{{ fresh.password }}</code>
    <div class="row">
      <button type="button" class="btn btn-sm" @click="copyFresh"><Icon name="copy" :size="14" />Copy</button>
      <button type="button" class="btn btn-sm" data-testid="temp-password-done" @click="fresh = null">Done</button>
    </div>
  </section>

  <form v-if="adding" class="panel add" data-testid="add-user-form" @submit.prevent="create">
    <h2>New user</h2>
    <div class="grid">
      <label>
        <span>Username</span>
        <input v-model.trim="form.username" class="input mono" autocomplete="off" placeholder="dana" data-testid="new-username" />
        <small v-if="form.username && !usernameOk" class="err">3 to 40 letters, digits, ".", "_" or "-"</small>
      </label>
      <label>
        <span>Name shown in the dashboard</span>
        <input v-model="form.display_name" class="input" maxlength="80" placeholder="Dana Cohen" data-testid="new-display-name" />
      </label>
      <label>
        <span>Role</span>
        <select v-model="form.role" class="select" data-testid="new-role">
          <option v-for="r in ROLES" :key="r" :value="r">{{ ROLE_LABEL[r] }}</option>
        </select>
        <small class="muted">{{ ROLE_HELP[form.role] }}</small>
      </label>
    </div>
    <div class="row">
      <button type="submit" class="btn btn-primary" :disabled="!usernameOk || busy === 'new'" data-testid="create-user">Create and get a password</button>
      <button type="button" class="btn" @click="adding = false">Cancel</button>
    </div>
  </form>

  <p v-if="error" class="err" role="alert">{{ error }}</p>

  <section class="panel" data-tour="users-table">
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr><th>Name</th><th>Role</th><th>Status</th><th>Last sign-in</th><th>Created</th><th /></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" :class="{ off: u.disabled }" :data-testid="`user-${u.username}`">
            <td>
              <b>{{ u.display_name || u.username }}</b>
              <span class="muted small"> · {{ u.username }}<template v-if="isMe(u)"> · you</template></span>
            </td>
            <td>
              <select
                class="select small-select"
                :value="u.role"
                :disabled="isMe(u) || lastOwner(u) || busy === u.id"
                :title="isMe(u) ? 'Ask another owner to change your role' : lastOwner(u) ? 'The last owner stays an owner' : ROLE_HELP[u.role]"
                :aria-label="`Role of ${u.username}`"
                :data-testid="`role-${u.username}`"
                @change="setRole(u, ($event.target as HTMLSelectElement).value as Role)"
              >
                <option v-for="r in ROLES" :key="r" :value="r">{{ ROLE_LABEL[r] }}</option>
              </select>
            </td>
            <td>
              <StatusPill v-if="u.disabled" tone="idle" label="Disabled" />
              <StatusPill v-else-if="u.must_change_password" tone="warn" label="Waiting for first sign-in" />
              <StatusPill v-else tone="ok" label="Active" />
            </td>
            <td class="num">{{ u.last_login_at ? dateTime(u.last_login_at) : 'never' }}</td>
            <td class="num small">{{ dateTime(u.created_at) }}<span class="muted"> · {{ u.created_by }}</span></td>
            <td class="actions">
              <button type="button" class="btn btn-sm" :disabled="isMe(u) || busy === u.id" :data-testid="`reset-${u.username}`" @click="resetPassword(u)">Reset password</button>
              <button
                type="button"
                class="btn btn-sm"
                :class="{ 'btn-danger': !u.disabled }"
                :disabled="isMe(u) || lastOwner(u) || busy === u.id"
                :data-testid="`toggle-${u.username}`"
                @click="toggleDisabled(u)"
              >
                {{ u.disabled ? 'Enable' : 'Disable' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="loaded && !users.length && !error" class="empty">No users yet.</p>
    </div>
  </section>

  <section class="panel roles" data-tour="users-roles">
    <h2>What each role can do</h2>
    <dl>
      <template v-for="r in ROLES" :key="r">
        <dt>{{ ROLE_LABEL[r] }}</dt>
        <dd>{{ ROLE_HELP[r] }}</dd>
      </template>
    </dl>
  </section>
</template>

<style scoped>
.small {
  font-size: 12px;
}
.err {
  color: var(--danger);
}
.fresh {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  border-color: var(--warn);
  background: var(--warn-bg);
}
.fresh p {
  margin: 4px 0 0;
}
.pw {
  font: 600 18px var(--font-mono);
  letter-spacing: 0.04em;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg);
  border: 1px solid var(--border);
}
.row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.add h2,
.roles h2 {
  font: 700 16px var(--font-display);
  margin: 0 0 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px 16px;
  margin-bottom: 14px;
}
.grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.grid label > span {
  font-weight: 600;
}
.small-select {
  min-height: 32px;
  padding: 2px 8px;
}
tr.off td {
  color: var(--muted);
}
.actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
  flex-wrap: wrap;
}
.roles dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 6px 16px;
  margin: 0;
  font-size: 13px;
}
.roles dt {
  font-weight: 600;
}
.roles dd {
  margin: 0;
  color: var(--muted);
}
</style>
