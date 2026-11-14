<script setup lang="ts">
// Agent health from the heartbeats KV (every 10 s, expires after 30 s) and queue lag from the agents' durable
// JetStream consumers (gateway/app/system.py).
import StatusPill from '@/components/ui/StatusPill.vue'
import { count, duration } from '@/lib/format'
import type { AgentRow, Tone } from '@/lib/types'

withDefaults(defineProps<{ agents: AgentRow[]; detailed?: boolean }>(), { detailed: false })

const TONE: Record<AgentRow['status'], Tone> = { healthy: 'ok', late: 'warn', down: 'danger', planned: 'idle' }
const LABEL: Record<AgentRow['status'], string> = { healthy: 'Healthy', late: 'Late', down: 'Down', planned: 'Planned' }
const HINT: Record<AgentRow['status'], string> = {
  healthy: 'Heartbeat within the last 25 s',
  late: 'No heartbeat for more than 25 s',
  down: 'No heartbeat: the agent is not running',
  planned: 'Not built yet',
}

function beat(a: AgentRow): string {
  if (a.heartbeat_age_s == null) return '—'
  return a.heartbeat_age_s < 10 ? `${a.heartbeat_age_s.toFixed(1)} s` : `${Math.round(a.heartbeat_age_s)} s`
}
</script>

<template>
  <div class="table-wrap">
    <table class="data agents">
      <thead>
        <tr>
          <th scope="col">Agent</th>
          <th scope="col">Tier</th>
          <th scope="col">Status</th>
          <th scope="col">Heartbeat</th>
          <th scope="col">Version</th>
          <th scope="col" class="num" title="Messages waiting in or being handled by the agent's consumers">Queue</th>
          <template v-if="detailed">
            <th scope="col">Host</th>
            <th scope="col">Uptime</th>
          </template>
        </tr>
      </thead>
      <tbody>
        <tr v-for="a in agents" :key="a.name" :data-testid="`agent-${a.name}`" :data-status="a.status">
          <td>
            <span class="agent">{{ a.label }}</span>
            <span v-if="a.detail" class="detail">{{ a.detail }}</span>
          </td>
          <td><span class="chip">{{ a.tier }}</span></td>
          <td :title="HINT[a.status]"><StatusPill :tone="TONE[a.status]" :label="LABEL[a.status]" /></td>
          <td class="num">{{ beat(a) }}</td>
          <td class="num">{{ a.version ?? '—' }}</td>
          <td class="num" :class="{ 'text-warn': (a.queue ?? 0) > 50 }">{{ count(a.queue) }}</td>
          <template v-if="detailed">
            <td class="mono">{{ a.host ?? '—' }}</td>
            <td class="num">{{ duration(a.uptime_s) }}</td>
          </template>
        </tr>
      </tbody>
    </table>
    <p v-if="!agents.length" class="empty">Waiting for the first heartbeat…</p>
  </div>
</template>

<style scoped>
.agents {
  min-width: 0;
}
.agents td {
  white-space: nowrap;
}
.agents td:first-child {
  white-space: normal;
}
.agent {
  display: block;
  font-weight: 500;
}
.detail {
  display: block;
  font-size: 12px;
  color: var(--muted);
}
</style>
