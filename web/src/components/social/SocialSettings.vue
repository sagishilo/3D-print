<script setup lang="ts">
// Social settings (audited, like every setting) and the capture extension's tokens: one per browser or device,
// shown once when made, revocable. The extension sends only the post a person chose to capture.
import { computed, onMounted, reactive, ref } from 'vue'

import ToggleSwitch from '@/components/ui/ToggleSwitch.vue'
import { errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import { accuracyLine, copyText, spendLine } from '@/lib/social'
import { useConfirm } from '@/stores/confirm'
import { useSocial } from '@/stores/social'
import { useToasts } from '@/stores/toasts'
import { vCan } from '@/directives/can'

const social = useSocial()
const toasts = useToasts()
const confirm = useConfirm()

const numbers = computed(() => social.settings.filter((s) => typeof s.default === 'number'))
const enabled = computed(() => social.setting('social.enabled'))
const draft = reactive<Record<string, string>>({})
const label = ref('')
const fresh = ref<string | null>(null)

onMounted(() => void social.loadTokens().catch(() => undefined))

function shown(key: string, value: unknown) {
  return draft[key] ?? String(value ?? '')
}

async function save(key: string) {
  const s = social.setting(key)
  if (!s || draft[key] === undefined) return
  const value = Number(draft[key])
  if (!Number.isFinite(value)) {
    toasts.error('Not a number', s.label)
    return
  }
  try {
    await social.setSetting(key, value)
    delete draft[key]
    toasts.ok(`${s.label}: ${value}`, 'Saved and audited. The social agent applies it within about 5 s.')
  } catch (err) {
    toasts.error('Not saved', errorText(err))
  }
}

async function toggle(v: boolean) {
  try {
    await social.setSetting('social.enabled', v)
  } catch (err) {
    toasts.error('Not saved', errorText(err))
  }
}

async function makeToken() {
  if (!label.value.trim()) return
  try {
    fresh.value = await social.createToken(label.value.trim())
    label.value = ''
  } catch (err) {
    toasts.error('No token made', errorText(err))
  }
}

async function revoke(id: string, name: string) {
  const ok = await confirm.ask({ title: `Revoke “${name}”?`, text: 'That browser can no longer send captures until it gets a new token.',
    confirmLabel: 'Revoke', danger: true })
  if (!ok) return
  try {
    await social.revokeToken(id)
  } catch (err) {
    toasts.error('Not revoked', errorText(err))
  }
}
</script>

<template>
  <section class="panel" data-testid="social-settings">
    <div class="panel-head">
      <h2>Listening settings</h2>
      <span class="panel-note">changes are audited</span>
    </div>
    <div class="row">
      <div class="text">
        <span class="label">{{ enabled?.label ?? 'Social listening' }}</span>
        <span class="help">{{ enabled?.help }}</span>
      </div>
      <ToggleSwitch v-if="enabled" :model-value="Boolean(enabled.value)" :label="enabled.label" @update:model-value="toggle" />
    </div>
    <form v-for="s in numbers" :key="s.key" class="row" @submit.prevent="save(s.key)">
      <div class="text">
        <label class="label" :for="`set-${s.key}`">{{ s.label }}</label>
        <span class="help">{{ s.help }}</span>
      </div>
      <div class="num">
        <input :id="`set-${s.key}`" class="input mono" inputmode="decimal" :value="shown(s.key, s.value)" :data-testid="`set-${s.key}`"
          @input="draft[s.key] = ($event.target as HTMLInputElement).value" />
        <button v-can="'owner'" type="submit" class="btn btn-sm" :disabled="draft[s.key] === undefined">Save</button>
      </div>
    </form>
    <p class="panel-note">Spend: {{ spendLine(social.stats, social.budget) }}</p>
    <p class="panel-note" data-testid="accuracy">Accuracy: {{ accuracyLine(social.stats?.accuracy) }}</p>

    <div class="panel-head sub" data-tour="capture-extension">
      <h2>Capture extension</h2>
      <span class="panel-note">one token per browser</span>
    </div>
    <p class="help">
      Install the extension from the <code>extension/</code> folder (Chrome › Extensions › Developer mode › Load unpacked), open its
      options, enter this dashboard's address and a token from here. In a group, select a post's text, right-click › <b>Capture post to
      PrintFarm</b>. Only what you select is sent; nothing is read from the page by itself.
    </p>
    <form class="token-form" @submit.prevent="makeToken">
      <input v-model="label" class="input" maxlength="60" placeholder="e.g. my laptop" aria-label="Token name" data-testid="token-label" />
      <button v-can="'owner'" type="submit" class="btn btn-sm" :disabled="!label.trim()">New token</button>
    </form>
    <div v-if="fresh" class="fresh" role="status" data-testid="token-fresh">
      <span>Copy it now: it is shown only once.</span>
      <code class="mono">{{ fresh }}</code>
      <div>
        <button type="button" class="btn btn-sm" @click="copyText(fresh!).then((ok) => ok && toasts.ok('Token copied', ''))">Copy</button>
        <button type="button" class="btn btn-sm" @click="fresh = null">Done</button>
      </div>
    </div>
    <ul class="tokens">
      <li v-for="t in social.tokens" :key="t.token_id" :class="{ revoked: t.revoked_at }">
        <span><b>{{ t.label }}</b> <span class="muted small">· {{ t.uses }} capture{{ t.uses === 1 ? '' : 's' }}{{ t.last_used_at ? ` · last ${dateTime(t.last_used_at)}` : '' }}</span></span>
        <span v-if="t.revoked_at" class="muted small">revoked</span>
        <button v-can="'owner'" v-else type="button" class="btn btn-sm" @click="revoke(t.token_id, t.label)">Revoke</button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.label {
  font-weight: 500;
  font-size: 13px;
}
.help {
  font-size: 12px;
  color: var(--muted);
}
.num {
  display: flex;
  gap: 6px;
}
.num .input {
  width: 80px;
}
.sub {
  margin-top: 16px;
}
.token-form {
  display: flex;
  gap: 6px;
  margin: 8px 0;
}
.fresh {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--warn);
  border-radius: var(--radius-sm);
  font-size: 12px;
}
.fresh code {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.fresh div {
  display: flex;
  gap: 6px;
}
.tokens {
  list-style: none;
  padding: 0;
  margin: 0;
}
.tokens li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
}
.tokens .revoked {
  opacity: 0.5;
}
.small {
  font-size: 12px;
}
</style>
