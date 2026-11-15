<script setup lang="ts">
// The logged-in frame: sidebar, top bar, the screen, and the app-wide data that the shell itself shows
// (agents, printers, approvals, the live feed). Polls stop when the tab is hidden.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import { useApprovals } from '@/stores/approvals'
import { useInventory } from '@/stores/inventory'
import { useLive } from '@/stores/live'
import { usePrinters } from '@/stores/printers'
import { useSystem } from '@/stores/system'

import ApprovalsDrawer from './ApprovalsDrawer.vue'
import HealthBanner from './HealthBanner.vue'
import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'

const route = useRoute()
const live = useLive()
const printers = usePrinters()
const system = useSystem()
const approvals = useApprovals()

const navOpen = ref(false)
const approvalsOpen = ref(false)
let poll: ReturnType<typeof setInterval> | null = null

function refreshShell() {
  void system.loadAgents()
  void system.loadDlq()
  void approvals.load().catch(() => undefined)
  void useInventory().loadAlerts()
}

function onVisibility() {
  if (document.hidden) return
  refreshShell()
  void printers.load()
}

onMounted(() => {
  live.start()
  void printers.load()
  refreshShell()
  poll = setInterval(() => !document.hidden && refreshShell(), 10_000)
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  // The shell only unmounts when the session ends (logout or expiry): close the live feed with it.
  if (poll) clearInterval(poll)
  document.removeEventListener('visibilitychange', onVisibility)
  live.stop()
})

watch(
  () => route.fullPath,
  () => (navOpen.value = false),
)
</script>

<template>
  <div class="shell" :class="{ 'nav-open': navOpen }">
    <SideNav class="side" @navigate="navOpen = false" />
    <div v-if="navOpen" class="nav-scrim" @click="navOpen = false" />
    <div class="column">
      <TopBar @menu="navOpen = !navOpen" @approvals="approvalsOpen = true" />
      <HealthBanner />
      <main class="content">
        <RouterView />
      </main>
    </div>
    <ApprovalsDrawer :open="approvalsOpen" @close="approvalsOpen = false" />
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--sidebar) minmax(0, 1fr);
  min-height: 100vh;
}
.side {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}
.column {
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.content {
  padding: 24px var(--gutter) 40px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 1440px;
  width: 100%;
}
.nav-scrim {
  display: none;
}
@media (max-width: 900px) {
  .shell {
    grid-template-columns: minmax(0, 1fr);
  }
  .side {
    position: fixed;
    inset: 0 auto 0 0;
    width: min(280px, 85vw);
    z-index: 35;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }
  .nav-open .side {
    transform: none;
  }
  .nav-open .nav-scrim {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 34;
  }
}
</style>
