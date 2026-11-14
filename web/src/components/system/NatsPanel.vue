<script setup lang="ts">
// NATS clients (monitoring endpoint /connz) and JetStream streams. Every agent process connects as
// "agent:<names>", the gateway as "gateway", the CLI as "printfarm-cli".
import { computed } from 'vue'

import { bytes, count, duration } from '@/lib/format'
import type { NatsConnection, StreamRow } from '@/lib/types'

const props = defineProps<{ connections: NatsConnection[]; streams: StreamRow[] }>()

const sorted = computed(() => [...props.connections].sort((a, b) => (a.name ?? '~').localeCompare(b.name ?? '~')))

function keeps(s: StreamRow): string {
  const age = s.max_age_s
  if (!age) return s.name === 'COMMANDS' ? 'until handled' : 'no limit'
  if (age >= 86400) return `${Math.round(age / 86400)} d`
  if (age >= 3600) return `${Math.round(age / 3600)} h`
  return duration(age)
}

function role(name: string | null): string {
  if (!name) return 'unnamed client'
  if (name.startsWith('agent:')) return 'agents'
  if (name === 'gateway') return 'dashboard API'
  if (name === 'printfarm-cli') return 'CLI'
  if (name.startsWith('finance')) return 'finance'
  return ''
}
</script>

<template>
  <section class="panel" data-testid="nats-connections">
    <div class="panel-head">
      <h2>NATS connections</h2>
      <span class="panel-note">{{ connections.length }} client{{ connections.length === 1 ? '' : 's' }} · from the monitoring endpoint</span>
    </div>
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th scope="col">Client</th>
            <th scope="col">Address</th>
            <th scope="col">Uptime</th>
            <th scope="col" class="num">Subs</th>
            <th scope="col" class="num">Msgs in</th>
            <th scope="col" class="num">Msgs out</th>
            <th scope="col">RTT</th>
            <th scope="col">Library</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in sorted" :key="c.cid">
            <td>
              <span class="mono">{{ c.name ?? `#${c.cid}` }}</span>
              <span v-if="role(c.name)" class="muted"> · {{ role(c.name) }}</span>
            </td>
            <td class="mono">{{ c.ip }}:{{ c.port }}</td>
            <td class="num">{{ c.uptime }}</td>
            <td class="num">{{ count(c.subscriptions) }}</td>
            <td class="num">{{ count(c.in_msgs) }}</td>
            <td class="num">{{ count(c.out_msgs) }}</td>
            <td class="num">{{ c.rtt ?? '—' }}</td>
            <td class="muted">{{ c.lang ? `${c.lang} ${c.version ?? ''}` : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!connections.length" class="empty">No connection data (is the NATS monitoring port reachable from the gateway?).</p>
    </div>
  </section>

  <section class="panel" data-testid="nats-streams">
    <div class="panel-head">
      <h2>JetStream streams</h2>
      <span class="panel-note">evt.* 30 days · cmd.* work queue · tlm.* 1 hour in memory · dlq.* 90 days</span>
    </div>
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th scope="col">Stream</th>
            <th scope="col">Subjects</th>
            <th scope="col">Storage</th>
            <th scope="col">Keeps</th>
            <th scope="col" class="num">Messages</th>
            <th scope="col" class="num">Size</th>
            <th scope="col" class="num">Consumers</th>
            <th scope="col" class="num">Sequence</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in streams" :key="s.name">
            <td class="mono">{{ s.name }}</td>
            <td class="mono muted">{{ s.subjects.join(' ') }}</td>
            <td>{{ s.storage }}</td>
            <td class="num">{{ keeps(s) }}</td>
            <td class="num">{{ count(s.messages) }}</td>
            <td class="num">{{ bytes(s.bytes) }}</td>
            <td class="num">{{ count(s.consumers) }}</td>
            <td class="num">{{ s.messages ? `${count(s.first_seq)} – ${count(s.last_seq)}` : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!streams.length" class="empty">Loading…</p>
    </div>
  </section>
</template>
