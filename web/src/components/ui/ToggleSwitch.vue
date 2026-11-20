<script setup lang="ts">
defineProps<{ label: string; disabled?: boolean }>()
const model = defineModel<boolean>({ required: true })
</script>

<template>
  <button
    type="button"
    role="switch"
    class="switch"
    :class="{ on: model }"
    :aria-checked="model"
    :aria-label="label"
    :disabled="disabled"
    @click="model = !model"
  >
    <span class="knob" />
  </button>
</template>

<style scoped>
.switch {
  width: 44px;
  height: 26px;
  border-radius: 999px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  position: relative;
  cursor: pointer;
  padding: 0;
  flex: none;
}
.switch.on {
  background: var(--accent);
  border-color: var(--accent);
}
.switch:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.knob {
  position: absolute;
  top: 3px;
  inset-inline-start: 3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--muted);
  transition: left 0.15s ease;
}
.switch.on .knob {
  inset-inline-start: 21px;
  background: var(--bg);
}
</style>
