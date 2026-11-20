<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import PageTourButton from '@/components/tour/PageTourButton.vue'
import CommandPalette from './CommandPalette.vue'
import NicheSwitcher from './NicheSwitcher.vue'
import UserMenu from './UserMenu.vue'
import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { command, errorText } from '@/lib/api'
import { useApprovals } from '@/stores/approvals'
import { vCan } from '@/directives/can'
import { useConfirm } from '@/stores/confirm'
import { useLive } from '@/stores/live'
import { usePrinters } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'

defineEmits<{ menu: []; approvals: [] }>()

const { t } = useI18n()
const route = useRoute()
// Every screen's page tour starts from the same place: the far right of the top bar (nothing beside it changes width).
const pageTour = computed(() => route.meta.tour)
const live = useLive()
const approvals = useApprovals()
const printers = usePrinters()
const toasts = useToasts()
const confirm = useConfirm()

// ---------------------------------------------------------------- command palette (⌘K / Ctrl+K, or the search box)
const paletteOpen = ref(false)
function onGlobalKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    paletteOpen.value = !paletteOpen.value
  }
}
onMounted(() => window.addEventListener('keydown', onGlobalKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKey))

// ---------------------------------------------------------------- actions
const pausing = ref(false)
async function pauseAll() {
  const printing = printers.counts.printing + printers.counts.heating
  const ok = await confirm.ask({
    title: 'Pause all printers?',
    text: `Every printer that is printing now (${printing}) is paused. Each pause is checked against the printer's own state; you get an alert for any printer that does not stop.`,
    confirmLabel: 'Pause all',
    danger: true,
  })
  if (!ok) return
  pausing.value = true
  try {
    await command('printers.pause_all', { reason: 'pause all from the dashboard' })
    toasts.info('Pausing all printers', 'Confirmations arrive in the live feed.')
  } catch (err) {
    toasts.error('Pause all failed', errorText(err))
  } finally {
    pausing.value = false
  }
}

</script>

<template>
  <header class="topbar">
    <button type="button" class="icon-btn menu" aria-label="Open navigation" @click="$emit('menu')"><Icon name="menu" :size="18" /></button>

    <div class="search">
      <button type="button" class="search-box" aria-haspopup="dialog" :aria-label="t('topbar.palette')" data-testid="palette-open" data-tour="palette" @click="paletteOpen = true">
        <Icon name="search" />
        <span class="placeholder" aria-hidden="true">{{ t('topbar.search') }}</span>
        <kbd>⌘K</kbd>
      </button>
    </div>
    <CommandPalette v-model:open="paletteOpen" />

    <div class="right">
      <NicheSwitcher />
      <StatusPill :tone="live.tone" :label="live.label" class="wide" data-testid="live-indicator" data-tour="live-indicator" />
      <StatusPill :tone="live.tone" :label="live.shortLabel" class="narrow" aria-hidden="true" data-tour="live-indicator" />
      <button
        type="button"
        class="icon-btn"
        :aria-label="`Approvals inbox, ${approvals.count} pending`"
        data-testid="approvals-button"
        @click="$emit('approvals')"
      >
        <Icon name="inbox" :size="18" />
        <span v-if="approvals.count" class="count">{{ approvals.count }}</span>
      </button>
      <button type="button" class="btn btn-danger" :disabled="pausing" :aria-label="t('topbar.pauseAll')" data-testid="pause-all" v-can="'operator'" @click="pauseAll">
        <Icon name="pause" :size="14" /><span class="wide">{{ t('topbar.pauseAll') }}</span><span class="narrow">{{ t('topbar.pauseAllShort') }}</span>
      </button>
      <UserMenu />
      <PageTourButton v-if="pageTour" :key="pageTour" :tour="pageTour" />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px var(--gutter);
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  flex-wrap: wrap;
  position: sticky;
  top: 0;
  z-index: 20;
}
.menu {
  display: none;
}
.search {
  position: relative;
  flex: 1 1 260px;
  max-width: 420px;
}
.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--muted);
}
.search-box {
  width: 100%;
  cursor: pointer;
  font: inherit;
}
.placeholder {
  flex: 1;
  text-align: left;
  font-size: 13px;
}
.search-box input {
  flex: 1;
  min-width: 0;
  background: transparent;
  border: 0;
  outline: none;
  font-size: 13px;
}
kbd {
  font: 500 11px var(--font-mono);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
}
.results {
  position: absolute;
  top: 44px;
  left: 0;
  right: 0;
  list-style: none;
  margin: 0;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  z-index: 30;
}
.results button {
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  background: none;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
}
.results button:hover {
  background: var(--surface-2);
}
.right {
  margin-left: auto;
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.count {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 18px;
  height: 18px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--on-accent);
  font: 700 11px var(--font);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
}
@media (max-width: 900px) {
  .menu {
    display: inline-flex;
  }
}
.narrow {
  display: none;
}
/* Phones: one row of controls (menu, live, approvals, pause all, log out), search below. */
@media (max-width: 600px) {
  .topbar {
    gap: 8px;
  }
  .search {
    order: 3;
    flex-basis: 100%;
    max-width: none;
  }
  .right {
    gap: 4px;
    flex-wrap: nowrap;
  }
  .wide {
    display: none;
  }
  .narrow {
    display: inline-flex;
  }
  .btn-danger {
    padding: 8px 8px;
  }
}
</style>
