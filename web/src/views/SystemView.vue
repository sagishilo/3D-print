<script setup lang="ts">
// System & Agents: agent health, NATS (clients, streams), servers, printer connections, dead letters and the
// automation settings. Polls every 5 s while the screen is open and the tab is visible.
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AgentsTable from '@/components/system/AgentsTable.vue'
import AuditPanel from '@/components/system/AuditPanel.vue'
import BackupsPanel from '@/components/system/BackupsPanel.vue'
import DlqPanel from '@/components/system/DlqPanel.vue'
import NatsPanel from '@/components/system/NatsPanel.vue'
import PrintersPanel from '@/components/system/PrintersPanel.vue'
import ServersPanel from '@/components/system/ServersPanel.vue'
import SettingsPanel from '@/components/system/SettingsPanel.vue'
import Icon from '@/components/ui/Icon.vue'
import KpiCard from '@/components/ui/KpiCard.vue'
import TabBar from '@/components/ui/TabBar.vue'
import { count, duration } from '@/lib/format'
import { useAuth } from '@/stores/auth'
import { useSystem } from '@/stores/system'

type TabId = 'overview' | 'agents' | 'nats' | 'dlq' | 'config' | 'audit'
const TAB_IDS: TabId[] = ['overview', 'agents', 'nats', 'dlq', 'config', 'audit']

const route = useRoute()
const router = useRouter()
const system = useSystem()
const auth = useAuth()

const tabs = computed(() => [
  { id: 'overview', label: 'Overview' },
  { id: 'agents', label: 'Agents' },
  { id: 'nats', label: 'NATS' },
  { id: 'dlq', label: `DLQ · ${system.dlqTotal}` },
  { id: 'config', label: 'Config' },
  ...(auth.can('owner') ? [{ id: 'audit', label: 'Audit log' }] : []),   // owners only (the gateway checks too)
])
const tab = computed<TabId>({
  get: () => {
    const t = route.query.tab as TabId
    return TAB_IDS.includes(t) && (t !== 'audit' || auth.can('owner')) ? t : 'overview'
  },
  set: (t) => void router.replace({ query: t === 'overview' ? {} : { tab: t } }),
})

async function refresh() {
  const jobs: Promise<unknown>[] = [system.loadOverview(), system.loadAgents()]
  if (tab.value === 'nats') jobs.push(system.loadConnections(), system.loadStreams())
  await Promise.all(jobs)
}
let poll: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  void refresh()
  void system.loadSettings()
  void system.loadDlq()
  poll = setInterval(() => !document.hidden && void refresh(), 5_000)
})
onBeforeUnmount(() => {
  if (poll) clearInterval(poll)
})
watch(tab, (t) => {
  if (t === 'nats') void Promise.all([system.loadConnections(), system.loadStreams()])
  if (t === 'dlq') void system.loadDlq()
  if (t === 'config') void system.loadSettings()
})

const o = computed(() => system.overview)
const dlqLastHour = computed(() => system.dlq.filter((d) => d.time && Date.now() - new Date(d.time).getTime() < 3_600_000).length)
const kpis = computed(() => {
  const v = o.value
  return [
    {
      label: 'NATS msgs / s',
      value: v?.nats.msgs_per_s != null ? String(v.nats.msgs_per_s) : '—',
      sub: v ? `${count(v.nats.connections)} client connections` : '',
    },
    {
      label: 'Consumer lag',
      value: count(v?.consumer_lag.total),
      sub: v?.consumer_lag.max ? `max: ${v.consumer_lag.max_agent} (${v.consumer_lag.max})` : 'all caught up',
      subTone: (v?.consumer_lag.total ?? 0) > 100 ? ('warn' as const) : ('idle' as const),
    },
    {
      label: 'Postgres conns',
      value: v?.postgres.ok ? `${v.postgres.connections} / ${v.postgres.max_connections}` : '—',
      sub: v?.postgres.ok ? `slow queries ${v.postgres.slow_queries}` : 'unreachable',
      subTone: !v?.postgres.ok || (v.postgres.slow_queries ?? 0) > 0 ? ('warn' as const) : ('idle' as const),
    },
    {
      label: 'WS clients',
      value: count(v?.gateway.ws_clients),
      sub: v ? `gateway up ${duration(v.gateway.uptime_s)}` : '',
    },
    {
      label: 'Dead letters',
      value: count(v?.dlq.messages),
      sub: `+${dlqLastHour.value} in the last hour`,
      subTone: dlqLastHour.value ? ('danger' as const) : ('idle' as const),
    },
  ]
})
</script>

<template>
  <div class="page-head">
    <div>
      <h1>System &amp; Agents</h1>
      <p>Agent health, NATS JetStream, dead-letter queue and automation config</p>
    </div>
    <button type="button" class="btn" data-tour="system-refresh" @click="refresh"><Icon name="refresh" :size="14" />Refresh</button>
  </div>

  <TabBar v-model="tab" :tabs="tabs" label="System sections" data-tour="system-tabs" />

  <p v-if="system.error" class="error" role="alert"><Icon name="alert" :size="14" /> {{ system.error }}</p>

  <template v-if="tab === 'overview'">
    <div class="kpis" data-tour="system-kpis">
      <KpiCard v-for="k in kpis" :key="k.label" :label="k.label" :value="k.value" :sub="k.sub" :sub-tone="k.subTone" />
    </div>
    <div class="wide-narrow">
      <section class="panel" data-tour="agents-panel">
        <div class="panel-head">
          <h2>Agents</h2>
          <span class="panel-note">heartbeat every 10 s via the heartbeats KV</span>
        </div>
        <AgentsTable :agents="system.agents" />
      </section>
      <SettingsPanel />
    </div>
    <div class="grid-2">
      <ServersPanel :overview="system.overview" />
      <PrintersPanel />
    </div>
    <BackupsPanel />
    <DlqPanel :limit="5" />
  </template>

  <template v-else-if="tab === 'agents'">
    <section class="panel">
      <div class="panel-head">
        <h2>Agents</h2>
        <span class="panel-note">Healthy = heartbeat within 25 s · Queue = messages waiting in the agent's JetStream consumers</span>
      </div>
      <AgentsTable :agents="system.agents" detailed />
    </section>
  </template>

  <template v-else-if="tab === 'nats'">
    <div class="kpis">
      <KpiCard v-for="k in kpis.slice(0, 2)" :key="k.label" :label="k.label" :value="k.value" :sub="k.sub" :sub-tone="k.subTone" />
      <KpiCard label="JetStream messages" :value="count(o?.nats.jetstream.messages)" :sub="`${count(o?.nats.jetstream.streams)} streams`" />
      <KpiCard label="NATS version" :value="o?.nats.version ?? '—'" :sub="o?.nats.uptime ? `up ${o.nats.uptime}` : ''" />
    </div>
    <NatsPanel :connections="system.connections" :streams="system.streams" />
  </template>

  <DlqPanel v-else-if="tab === 'dlq'" />

  <AuditPanel v-else-if="tab === 'audit'" />

  <div v-else class="grid-2">
    <SettingsPanel />
  </div>
</template>

<style scoped>
.wide-narrow {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
@media (min-width: 1200px) {
  .wide-narrow {
    grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  }
}
.error {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--danger);
  font-size: 13px;
}
</style>
