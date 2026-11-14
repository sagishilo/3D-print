<script setup lang="ts">
// The servers behind the dashboard: gateway, NATS JetStream, Postgres and the go2rtc camera relay.
import { computed } from 'vue'

import StatusPill from '@/components/ui/StatusPill.vue'
import { bytes, count, duration } from '@/lib/format'
import type { Overview } from '@/lib/types'

const props = defineProps<{ overview: Overview | null }>()

const rows = computed(() => {
  const o = props.overview
  if (!o) return []
  const js = o.nats.jetstream
  return [
    {
      id: 'gateway',
      name: 'Gateway',
      ok: o.gateway.ok,
      version: o.gateway.version,
      detail: `${o.gateway.ws_clients} live feed client${o.gateway.ws_clients === 1 ? '' : 's'} · up ${duration(o.gateway.uptime_s)}`,
    },
    {
      id: 'nats',
      name: 'NATS JetStream',
      ok: o.nats.ok,
      version: o.nats.version,
      detail: o.nats.ok
        ? `${count(o.nats.connections)} connections · ${count(js.streams)} streams · ${count(js.consumers)} consumers · ${count(js.messages)} msgs (${bytes(js.bytes)}) · up ${o.nats.uptime ?? '—'}`
        : 'Monitoring endpoint (:8222) unreachable',
    },
    {
      id: 'postgres',
      name: 'Postgres',
      ok: o.postgres.ok,
      version: o.postgres.version ?? null,
      detail: o.postgres.ok
        ? `${o.postgres.connections} / ${o.postgres.max_connections} connections · ${o.postgres.slow_queries} slow quer${o.postgres.slow_queries === 1 ? 'y' : 'ies'}`
        : 'Unreachable',
    },
    {
      id: 'go2rtc',
      name: 'go2rtc (cameras)',
      ok: o.go2rtc.ok,
      version: o.go2rtc.version,
      detail: o.go2rtc.ok ? 'Camera relay: RTSP / RTSPS / MJPEG → MJPEG for the dashboard' : 'Unreachable: camera tiles stay offline',
    },
  ]
})
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>Servers</h2>
      <span class="panel-note">refreshes every 5 s</span>
    </div>
    <ul class="servers">
      <li v-for="r in rows" :key="r.id" :data-testid="`server-${r.id}`">
        <div class="top">
          <span class="name">{{ r.name }}</span>
          <span class="chip">{{ r.version ? `v${r.version}` : '—' }}</span>
          <StatusPill :tone="r.ok ? 'ok' : 'danger'" :label="r.ok ? 'OK' : 'Down'" />
        </div>
        <span class="detail">{{ r.detail }}</span>
      </li>
    </ul>
    <p v-if="!rows.length" class="empty">Loading…</p>
  </section>
</template>

<style scoped>
.servers {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.servers li {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.servers li:last-child {
  border-bottom: 0;
}
.top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.name {
  font-weight: 500;
  margin-right: auto;
}
.detail {
  font-size: 12px;
  color: var(--muted);
}
</style>
