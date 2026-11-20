<script setup lang="ts">
// Top bar: the niche the dashboard focuses on (a coloured chip). Catalog, Orders, Social and Finance then show only
// that niche, with a "Filtered" bar to show everything again. The choice is saved per person.
import { onBeforeUnmount, onMounted, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { nicheColor } from '@/lib/niches'
import { useNiche } from '@/stores/niche'

const niche = useNiche()
const open = ref(false)
const root = ref<HTMLElement | null>(null)

function pick(slug: string | null) {
  open.value = false
  void niche.set(slug)
}
function outside(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => {
  if (!niche.loaded) void niche.load()
  document.addEventListener('mousedown', outside)
})
onBeforeUnmount(() => document.removeEventListener('mousedown', outside))
</script>

<template>
  <div ref="root" class="niche-switcher" data-tour="niche-switcher" @keydown.esc="open = false">
    <button
      type="button"
      class="chip"
      :class="{ on: !!niche.current }"
      :aria-expanded="open"
      aria-haspopup="listbox"
      :aria-label="`Niche: ${niche.name(niche.current)}. Change niche`"
      data-testid="niche-switcher"
      @click="open = !open"
    >
      <span class="dot" :style="{ background: niche.current ? nicheColor(niche.active) : 'transparent' }" :class="{ ring: !niche.current }" />
      <span class="label">{{ niche.name(niche.current) }}</span>
      <Icon name="chevron-down" :size="12" class="chev" />
    </button>
    <ul v-if="open" class="menu" role="listbox" aria-label="Niche">
      <li>
        <button type="button" role="option" :aria-selected="!niche.current" data-testid="niche-option-all" @click="pick(null)">
          <span class="dot ring" />All niches
        </button>
      </li>
      <li v-for="n in niche.list" :key="n.slug">
        <button type="button" role="option" :aria-selected="niche.current === n.slug" :data-testid="`niche-option-${n.slug}`" @click="pick(n.slug)">
          <span class="dot" :style="{ background: nicheColor(n) }" />{{ n.name }}
          <span class="n muted">{{ n.products }}</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.niche-switcher {
  position: relative;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: var(--control-h);
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  max-width: 220px;
}
.chip.on {
  border-color: var(--accent-line);
}
.label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
}
.dot.ring {
  border: 2px solid var(--muted);
  background: transparent;
}
.menu {
  position: absolute;
  top: calc(100% + 6px);
  inset-inline-end: 0;
  min-width: 220px;
  list-style: none;
  margin: 0;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  z-index: 40;
}
.menu button {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: none;
  border: 0;
  border-radius: 6px;
  color: var(--text);
  text-align: start;
  cursor: pointer;
  font-size: 13px;
}
.menu button:hover,
.menu button[aria-selected='true'] {
  background: var(--surface-2);
}
.n {
  margin-inline-start: auto;
  font-size: 12px;
}
/* Phones: the top bar has one row of controls: the coloured dot only (the label names it for screen readers). */
@media (max-width: 600px) {
  .label,
  .chev {
    display: none;
  }
  .chip {
    width: 32px;
    padding: 0;
    justify-content: center;
  }
  .menu {
    position: fixed;
    top: 56px;
    inset-inline-end: 8px;
    inset-inline-start: 8px;
    min-width: 0;
  }
}
</style>
