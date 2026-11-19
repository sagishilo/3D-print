<script setup lang="ts">
// ⌘K / Ctrl+K: go anywhere and do anything by typing (lib/actions.ts). Arrows choose, Enter runs, Esc closes.
// Actions that change the farm are confirmed first; the palette never does more than the buttons it stands for.
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Icon from '@/components/ui/Icon.vue'
import { buildActions, type PaletteAction, search } from '@/lib/actions'
import { command, errorText, printerCommand } from '@/lib/api'
import { NAV } from '@/nav'
import { useAuth } from '@/stores/auth'
import { useCatalog } from '@/stores/catalog'
import { useConfirm } from '@/stores/confirm'
import { useNiche } from '@/stores/niche'
import { useOrders } from '@/stores/orders'
import { usePrefs } from '@/stores/prefs'
import { usePrinters } from '@/stores/printers'
import { useSystem } from '@/stores/system'
import { useToasts } from '@/stores/toasts'
import { useTour } from '@/stores/tour'
import type { TourId } from '@/lib/page-tours'

const open = defineModel<boolean>('open', { required: true })
const router = useRouter()
const route = useRoute()
const auth = useAuth()
const prefs = usePrefs()
const printers = usePrinters()
const orders = useOrders()
const catalog = useCatalog()
const niche = useNiche()
const system = useSystem()
const tour = useTour()
const confirm = useConfirm()
const toasts = useToasts()

const query = ref('')
const index = ref(0)
const input = ref<HTMLInputElement | null>(null)

watch(open, async (o) => {
  if (!o) return
  query.value = ''
  index.value = 0
  // What the palette searches: loaded on first use, quietly (the screens load them anyway).
  if (!orders.loadedAt) void orders.load()
  if (!catalog.loaded) void catalog.load()
  if (!niche.loaded) void niche.load()
  await nextTick()
  input.value?.focus()
})

const actions = computed(() => buildActions({
  can: auth.can, expert: prefs.expert, go: (to) => router.push(to),
  screens: [...NAV.filter((n) => !n.phase), { to: '/help', label: 'User guide' }],
  printers: printers.list.map((p) => ({ id: p.id, status: p.state?.status ?? (p.online ? 'idle' : 'offline') })),
  orders: orders.cards.filter((o) => !['SHIPPED', 'CANCELLED'].includes(o.state)),
  products: catalog.products, niches: niche.list, agents: system.agents,
  currentTour: (route.meta.tour as string | undefined) ?? null,
  pausePrinter: (id) => printerCommand(id, 'pause', { reason: 'paused from the command palette' }),
  resumePrinter: (id) => printerCommand(id, 'resume'),
  pauseAll: () => command('printers.pause_all', { reason: 'pause all from the command palette' }),
  setNiche: (slug) => niche.set(slug), setPrefs: (p) => prefs.set(p),
  startTour: (id) => tour.start(id as TourId), openApprovals: () => window.dispatchEvent(new CustomEvent('pf:open-approvals')),
}))
const results = computed(() => search(actions.value, query.value))
watch(query, () => (index.value = 0))

async function run(a: PaletteAction | undefined) {
  if (!a) return
  open.value = false
  if (a.confirm) {
    const ok = await confirm.ask({ title: a.confirm.title, text: a.confirm.text, confirmLabel: a.confirm.label, danger: a.confirm.danger })
    if (!ok) return
  }
  try {
    await a.run()
    if (a.confirm) toasts.info(`${a.label}: sent`, 'Confirmations arrive in the live feed.')
  } catch (err) {
    toasts.error(`${a.label} failed`, errorText(err))
  }
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    index.value = Math.min(index.value + 1, results.value.length - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    index.value = Math.max(index.value - 1, 0)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    void run(results.value[index.value])
  } else if (e.key === 'Escape') {
    open.value = false
  }
}
</script>

<template>
  <div v-if="open" class="scrim" @click.self="open = false">
    <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette" data-testid="palette">
      <label class="field">
        <Icon name="search" />
        <span class="sr-only">Search screens, actions, printers, orders, products</span>
        <input ref="input" v-model="query" type="text" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true"
          aria-controls="palette-list" :aria-activedescendant="results[index] ? `pa-${index}` : undefined"
          placeholder="Go to, do, or find: new order, a printer, a SKU, dark…" data-testid="palette-input" @keydown="onKey" />
        <kbd>Esc</kbd>
      </label>
      <ul id="palette-list" class="list" role="listbox">
        <li v-for="(a, i) in results" :id="`pa-${i}`" :key="a.id" role="option" :aria-selected="i === index"
          :class="{ on: i === index }" :data-testid="`palette-item-${a.id}`" @mousemove="index = i" @mousedown.prevent="run(a)">
          <span class="label">{{ a.label }}</span>
          <span class="muted small">{{ a.hint }}</span>
          <span class="group">{{ a.group }}</span>
        </li>
        <li v-if="!results.length" class="none muted">Nothing matches “{{ query }}”.</li>
      </ul>
      <p class="foot muted small">↑ ↓ choose · Enter run · Esc close</p>
    </div>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  z-index: 60;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 12vh 16px 16px;
}
.palette {
  width: min(620px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.field {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--muted);
}
.field input {
  flex: 1;
  min-width: 0;
  background: transparent;
  border: 0;
  outline: none;
  font-size: 15px;
  color: var(--text);
}
kbd {
  font: 500 11px var(--font-mono);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
}
.list {
  list-style: none;
  margin: 0;
  padding: 6px;
  max-height: 50vh;
  overflow: auto;
}
.list li {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}
.list li.on {
  background: var(--surface-2);
}
.label {
  color: var(--text);
}
.group {
  margin-left: auto;
  font-size: 11px;
  color: var(--muted);
}
.none {
  cursor: default;
}
.foot {
  margin: 0;
  padding: 8px 14px;
  border-top: 1px solid var(--border);
}
</style>
