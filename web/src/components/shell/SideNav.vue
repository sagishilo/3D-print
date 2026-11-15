<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import Icon from '@/components/ui/Icon.vue'
import { GROUPS, NAV } from '@/nav'
import { usePrinters } from '@/stores/printers'
import { useInventory } from '@/stores/inventory'
import { useSystem } from '@/stores/system'

defineEmits<{ navigate: [] }>()

const system = useSystem()
const printers = usePrinters()
const inventory = useInventory()

const badges = computed<Record<string, { n: number; tone: string }>>(() => {
  const systemIssues = system.unhealthy + system.dlqTotal
  const printerIssues = printers.counts.failed
  return {
    '/system': { n: systemIssues, tone: 'danger' },
    '/live': { n: printerIssues, tone: 'danger' },
    '/fleet': { n: printerIssues, tone: 'danger' },
    '/filament': { n: inventory.openAlerts.length, tone: 'warn' },
  }
})

const AGENT_TONE: Record<string, string> = { healthy: 'ok', late: 'warn', down: 'danger', planned: 'idle' }
</script>

<template>
  <nav aria-label="Main" class="sidenav">
    <div class="brand">
      <div class="logo" aria-hidden="true"><Icon name="brand" :size="16" :stroke-width="2.2" /></div>
      <div class="brand-text">
        <span class="name">PrintFarm Ops</span>
        <span class="sub">Operator console</span>
      </div>
    </div>

    <div class="groups">
      <template v-for="g in GROUPS" :key="g">
        <span class="group">{{ g }}</span>
        <RouterLink
          v-for="item in NAV.filter((n) => n.group === g)"
          :key="item.to"
          :to="item.to"
          class="item"
          active-class="active"
          @click="$emit('navigate')"
        >
          <Icon :name="item.icon" />
          <span>{{ item.label }}</span>
          <span v-if="item.phase" class="soon" :title="`Arrives in phase ${item.phase}`">P{{ item.phase }}</span>
          <span v-else-if="badges[item.to]?.n" class="badge" :class="badges[item.to].tone">{{ badges[item.to].n }}</span>
        </RouterLink>
      </template>
    </div>

    <div class="agents">
      <span class="group">Agents</span>
      <RouterLink v-for="a in system.coreAgents" :key="a.name" :to="{ path: '/system', query: { tab: 'agents' } }" class="agent">
        <span class="dot" :class="AGENT_TONE[a.status]" aria-hidden="true" />{{ a.label }}
        <span class="sr-only">: {{ a.status }}</span>
      </RouterLink>
      <span v-if="!system.coreAgents.length" class="agent muted">Loading…</span>
      <RouterLink to="/help" class="agent help" data-testid="nav-help" @click="$emit('navigate')">User guide · <span lang="he">מדריך למשתמש</span></RouterLink>
    </div>
  </nav>
</template>

<style scoped>
.sidenav {
  background: var(--surface);
  border-right: 1px solid var(--border);
  padding: 20px 14px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-height: 100%;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 6px;
}
.logo {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--accent);
  color: var(--bg);
  display: flex;
  align-items: center;
  justify-content: center;
}
.brand-text {
  display: flex;
  flex-direction: column;
}
.name {
  font: 700 14px var(--font-display);
}
.sub {
  font-size: 11px;
  color: var(--muted);
}
.groups {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.group {
  font: 600 11px var(--font);
  color: var(--muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 10px 12px 4px;
}
.item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  text-decoration: none;
  font: 500 13px var(--font);
  color: var(--muted);
  border: 1px solid transparent;
}
.item:hover {
  color: var(--text);
}
.item.active {
  color: var(--text);
  background: var(--surface-2);
  border-color: var(--border);
}
.item.active :deep(svg) {
  color: var(--accent);
}
.item .badge {
  margin-left: auto;
}
.soon {
  margin-left: auto;
  font: 500 10px var(--font-mono);
  color: var(--idle);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0 4px;
}
.agents {
  margin-top: auto;
  border-top: 1px solid var(--border);
  padding: 14px 6px 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.agents .group {
  padding: 0;
}
.agent {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  text-decoration: none;
}
.agent.help {
  margin-top: 10px;
  color: var(--accent);
}
.agent .dot {
  width: 6px;
  height: 6px;
}
</style>
