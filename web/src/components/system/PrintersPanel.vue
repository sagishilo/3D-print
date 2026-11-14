<script setup lang="ts">
// Printer connections at a glance (state from the printer_status KV and the live telemetry feed).
import { RouterLink } from 'vue-router'

import StatusPill from '@/components/ui/StatusPill.vue'
import { ago, title } from '@/lib/format'
import { useLive } from '@/stores/live'
import { displayStatus, STATUS_TONE, usePrinters } from '@/stores/printers'

const printers = usePrinters()
const live = useLive()

function lastSeen(at: string | undefined): string {
  return at ? `${ago(new Date(at).getTime(), live.now)} ago` : '—'
}
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>Printers</h2>
      <span class="panel-note">telemetry via MQTT / Moonraker</span>
    </div>
    <div class="table-wrap">
      <table class="data printers">
        <thead>
          <tr>
            <th scope="col">Printer</th>
            <th scope="col">Connection</th>
            <th scope="col">State</th>
            <th scope="col">Camera</th>
            <th scope="col">Telemetry</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in printers.list" :key="p.id" :data-testid="`system-printer-${p.id}`">
            <td>
              <RouterLink :to="{ path: '/live', query: { printer: p.id } }">{{ p.id }}</RouterLink>
              <span v-if="p.simulated" class="chip warn sim">SIM</span>
              <span class="detail">{{ p.model ? p.model.toUpperCase() : p.vendor }} · {{ p.protocol }}</span>
            </td>
            <td><StatusPill :tone="p.online ? 'ok' : 'danger'" :label="p.online ? 'Online' : 'Offline'" /></td>
            <td><StatusPill :tone="STATUS_TONE[displayStatus(p)]" :label="title(displayStatus(p))" /></td>
            <td>{{ p.camera ? 'Yes' : 'None' }}</td>
            <td class="num">{{ lastSeen(p.state?.observed_at) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!printers.list.length" class="empty">{{ printers.error || 'No printers configured.' }}</p>
    </div>
  </section>
</template>

<style scoped>
.printers {
  min-width: 0;
}
.printers td {
  white-space: nowrap;
}
.sim {
  margin-left: 6px;
}
.detail {
  display: block;
  font-size: 12px;
  color: var(--muted);
}
</style>
