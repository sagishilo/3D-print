<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import { useConfirm } from '@/stores/confirm'

const confirm = useConfirm()
const dialog = ref<HTMLDialogElement | null>(null)
const checked = ref(false)

watch(
  () => confirm.current,
  async (req) => {
    checked.value = false
    await nextTick()
    if (req && dialog.value && !dialog.value.open) dialog.value.showModal?.()
    if (!req && dialog.value?.open) dialog.value.close()
  },
)
</script>

<template>
  <dialog ref="dialog" class="confirm" aria-labelledby="confirm-title" @cancel.prevent="confirm.answer(false)">
    <template v-if="confirm.current">
      <h2 id="confirm-title">{{ confirm.current.title }}</h2>
      <p>{{ confirm.current.text }}</p>
      <label v-if="confirm.current.requireCheck" class="check">
        <input v-model="checked" type="checkbox" data-testid="confirm-check" />
        <span>{{ confirm.current.requireCheck }}</span>
      </label>
      <div class="actions">
        <button type="button" class="btn" @click="confirm.answer(false)">Cancel</button>
        <button
          type="button"
          class="btn"
          :class="confirm.current.danger ? 'btn-danger' : 'btn-primary'"
          :disabled="!!confirm.current.requireCheck && !checked"
          autofocus
          @click="confirm.answer(true)"
        >
          {{ confirm.current.confirmLabel ?? 'Confirm' }}
        </button>
      </div>
    </template>
  </dialog>
</template>

<style scoped>
.confirm {
  width: min(440px, calc(100vw - 32px));
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}
.confirm::backdrop {
  background: rgba(0, 0, 0, 0.6);
}
h2 {
  font: 600 16px var(--font);
  margin-bottom: 8px;
}
p {
  color: var(--muted);
  font-size: 13px;
}
.check {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 14px;
  padding: 10px 12px;
  border: 1px solid var(--warn);
  border-radius: var(--radius-sm);
  background: var(--warn-bg);
  font-size: 13px;
  cursor: pointer;
}
.check input {
  margin-top: 2px;
  accent-color: var(--accent);
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}
</style>
