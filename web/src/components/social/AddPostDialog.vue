<script setup lang="ts">
// Paste a post by hand (a capture without the extension): the same pipeline scores it.
import { computed, onMounted, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { useSocial } from '@/stores/social'
import { useToasts } from '@/stores/toasts'

const emit = defineEmits<{ close: [] }>()
const social = useSocial()
const toasts = useToasts()
const text = ref('')
const group = ref('')
const url = ref('')
const busy = ref(false)
const error = ref('')
const box = ref<HTMLTextAreaElement | null>(null)

const invalid = computed(() => {
  if (!text.value.trim()) return 'paste the post text'
  if (url.value && !/^https:\/\/\S+$/.test(url.value.trim())) return 'the link must start with https://'
  return null
})

async function submit() {
  if (invalid.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await social.addPost(text.value.trim(), group.value.trim(), url.value.trim())
    toasts.info('Post added', 'The social agent scores it in a few seconds.')
    social.reloadSoon(1500)
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => box.value?.focus())
</script>

<template>
  <div class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="add-post-title" data-testid="add-post" @submit.prevent="submit">
      <header>
        <h2 id="add-post-title">Add post</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </header>
      <label>Post text<textarea ref="box" v-model="text" class="input" rows="5" maxlength="5000" dir="auto" data-testid="post-text" /></label>
      <label>Group<input v-model="group" class="input" maxlength="200" dir="auto" placeholder="e.g. הורים בחיפה" /></label>
      <label>Link to the post<input v-model="url" class="input" maxlength="1000" placeholder="https://www.facebook.com/groups/…" /></label>
      <p v-if="error || (invalid && text)" class="err" role="alert">{{ error || invalid }}</p>
      <footer>
        <button type="button" class="btn" @click="emit('close')">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!!invalid || busy" data-testid="add-post-submit">Add</button>
      </footer>
    </form>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  z-index: 45;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  overflow-y: auto;
  padding: 60px 16px;
}
.dialog {
  width: min(520px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
header,
footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
footer {
  justify-content: flex-end;
}
h2 {
  font: 700 17px var(--font-display);
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.err {
  color: var(--danger);
  font-size: 13px;
}
</style>
