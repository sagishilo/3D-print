<script setup lang="ts">
export interface Tab {
  id: string
  label: string
}

const props = defineProps<{ tabs: Tab[]; label: string }>()
const model = defineModel<string>({ required: true })

function onKey(e: KeyboardEvent) {
  const i = props.tabs.findIndex((t) => t.id === model.value)
  if (e.key === 'ArrowRight') model.value = props.tabs[(i + 1) % props.tabs.length].id
  if (e.key === 'ArrowLeft') model.value = props.tabs[(i - 1 + props.tabs.length) % props.tabs.length].id
}
</script>

<template>
  <div role="tablist" :aria-label="label" class="tabs" @keydown="onKey">
    <button
      v-for="t in tabs"
      :key="t.id"
      type="button"
      role="tab"
      :aria-selected="model === t.id"
      :data-tab="t.id"
      :tabindex="model === t.id ? 0 : -1"
      :class="{ active: model === t.id }"
      @click="model = t.id"
    >
      {{ t.label }}
    </button>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
button {
  min-height: 40px;
  padding: 0 14px;
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  font: 500 13px var(--font);
  cursor: pointer;
}
button.active {
  border-bottom-color: var(--accent);
  color: var(--text);
}
</style>
