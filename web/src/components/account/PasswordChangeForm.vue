<script setup lang="ts">
// Change your own password: after an owner set a temporary one (required before anything else), or any time from
// the user menu. The gateway ends your other sessions and starts a fresh one here.
import { computed, ref } from 'vue'

import { errorText } from '@/lib/api'
import { useAuth } from '@/stores/auth'

const props = defineProps<{ required?: boolean }>()
const emit = defineEmits<{ done: []; cancel: [] }>()
const auth = useAuth()

const current = ref('')
const next = ref('')
const again = ref('')
const busy = ref(false)
const error = ref('')

const problem = computed(() => {
  if (!next.value) return ''
  if (next.value.length < 12) return 'At least 12 characters (a few words work well).'
  if (next.value === current.value) return 'Choose a new password, not the current one.'
  if (again.value && again.value !== next.value) return 'The two new passwords are not the same.'
  return ''
})
const ready = computed(() => current.value && next.value && again.value === next.value && !problem.value)

async function submit() {
  if (!ready.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await auth.changePassword(current.value, next.value)
    emit('done')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="pw" novalidate data-testid="password-form" @submit.prevent="submit">
    <p v-if="props.required" class="lead" role="status">
      Your password was set by an owner. Choose your own to continue: only you will know it.
    </p>
    <label class="field">
      <span>{{ props.required ? 'Temporary password' : 'Current password' }}</span>
      <input v-model="current" class="input" type="password" autocomplete="current-password" data-testid="pw-current" required />
    </label>
    <label class="field">
      <span>New password</span>
      <input v-model="next" class="input" type="password" autocomplete="new-password" data-testid="pw-new" required />
    </label>
    <label class="field">
      <span>New password again</span>
      <input v-model="again" class="input" type="password" autocomplete="new-password" data-testid="pw-again" required />
    </label>
    <span v-if="problem || error" class="err" role="alert" data-testid="pw-error">{{ error || problem }}</span>
    <div class="actions">
      <button type="submit" class="btn btn-primary" :disabled="!ready || busy" data-testid="pw-save">
        {{ busy ? 'Saving…' : 'Save new password' }}
      </button>
      <button v-if="!props.required" type="button" class="btn" @click="emit('cancel')">Cancel</button>
    </div>
  </form>
</template>

<style scoped>
.pw {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.lead {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
}
.err {
  color: var(--danger);
  font-size: 13px;
}
.actions {
  display: flex;
  gap: 8px;
}
</style>
