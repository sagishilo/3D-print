<script setup lang="ts">
// Backups: the last local backup and the last encrypted off-site copy (GET /api/v1/system/backups, from the backup
// service's status.json). Refreshes every minute.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import StatusPill from '@/components/ui/StatusPill.vue'
import { api } from '@/lib/api'
import { ago, bytes } from '@/lib/format'
import type { Tone } from '@/lib/types'

export interface BackupStatus {
  known: boolean
  state: 'ok' | 'local_only' | 'problem' | 'unknown'
  problems: string[]
  interval_s?: number
  keep?: number
  offsite_enabled?: boolean
  offsite_remote?: string
  offsite_keep_days?: number
  local_ok_at?: string
  local_file?: string
  local_bytes?: number
  offsite_ok_at?: string
  offsite_file?: string
  drill_ok_at?: string
  drill_tables?: number
}

const status = ref<BackupStatus | null>(null)
const error = ref('')
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  try {
    status.value = await api<BackupStatus>('/api/v1/system/backups')
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
onMounted(() => {
  void load()
  timer = setInterval(() => !document.hidden && void load(), 60_000)
})
onBeforeUnmount(() => timer && clearInterval(timer))

const STATE: Record<BackupStatus['state'], { tone: Tone; label: string }> = {
  ok: { tone: 'ok', label: 'Protected' },
  local_only: { tone: 'warn', label: 'Local only' },
  problem: { tone: 'danger', label: 'Needs attention' },
  unknown: { tone: 'idle', label: 'No status' },
}
const when = (s?: string) => (s ? `${ago(Date.parse(s))} ago` : 'never')
const s = computed(() => status.value)
</script>

<template>
  <section class="panel" data-testid="backups" data-tour="backups">
    <div class="panel-head">
      <h2>Backups</h2>
      <StatusPill v-if="s" :tone="STATE[s.state].tone" :label="STATE[s.state].label" data-testid="backup-state" />
    </div>
    <p v-if="error" class="err" role="alert">{{ error }}</p>
    <template v-else-if="s">
      <ul v-if="s.problems.length" class="problems" role="alert">
        <li v-for="p in s.problems" :key="p">{{ p }}</li>
      </ul>
      <dl v-if="s.known" class="facts">
        <dt>Local copy</dt>
        <dd data-testid="backup-local">
          {{ when(s.local_ok_at) }}<template v-if="s.local_bytes"> · {{ bytes(s.local_bytes) }}</template>
          <span class="muted"> · keeps {{ s.keep }}</span>
        </dd>
        <dt>Off-site copy</dt>
        <dd data-testid="backup-offsite">
          <template v-if="s.offsite_enabled">
            {{ when(s.offsite_ok_at) }} · encrypted · {{ s.offsite_remote }}<span class="muted"> · keeps {{ s.offsite_keep_days }} days</span>
          </template>
          <template v-else>Not set up: copies stay on this PC only. README, "Off-site backups".</template>
        </dd>
        <dt>Restore drill</dt>
        <dd data-testid="backup-drill">
          <template v-if="s.drill_ok_at">{{ when(s.drill_ok_at) }} · {{ s.drill_tables }} tables restored</template>
          <template v-else>Never practised: <code>docker compose run --rm backup drill</code></template>
        </dd>
      </dl>
    </template>
    <p v-else class="muted">Loading…</p>
  </section>
</template>

<style scoped>
.facts {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 6px 16px;
  margin: 0;
  font-size: 13px;
}
.facts dt {
  color: var(--muted);
}
.facts dd {
  margin: 0;
}
.problems {
  margin: 0 0 10px;
  padding: 8px 12px 8px 26px;
  border-radius: var(--radius-sm);
  background: var(--danger-bg);
  color: var(--danger);
  font-size: 13px;
}
.err {
  color: var(--danger);
}
code {
  font: 12px var(--font-mono);
}
</style>
