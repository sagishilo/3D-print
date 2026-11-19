<script setup lang="ts">
// Audit log (owner only): who did what, and when. Every command from the dashboard, every sign-in (and failed one),
// user changes, dead-letter replays and connection tests. Filters by person, action and dates; newest first.
import { computed, onMounted, reactive, ref, watch } from 'vue'

import StatusPill from '@/components/ui/StatusPill.vue'
import { api, errorText } from '@/lib/api'
import { auditAction, auditDetail, auditTone, type AuditEntry } from '@/lib/audit'
import { dateTime } from '@/lib/format'

const entries = ref<AuditEntry[]>([])
const next = ref<number | null>(null)
const facets = ref<{ actors: string[]; actions: string[] }>({ actors: [], actions: [] })
const filters = reactive({ actor: '', action: '', outcome: '', since: '', until: '' })
const loading = ref(false)
const error = ref('')
const open = ref<number | null>(null)

const actionGroups = computed(() => {
  // the filter offers whole areas ("users", "auth") and each exact action
  const areas = new Set(facets.value.actions.map((a) => a.split('.').slice(0, a.startsWith('cmd.') ? 2 : 1).join('.')))
  return { areas: [...areas].sort(), actions: [...facets.value.actions].sort() }
})

function query(before?: number | null): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) if (v) q.set(k, v)
  if (before) q.set('before_id', String(before))
  return q.toString()
}

async function load(more = false) {
  loading.value = true
  error.value = ''
  try {
    const r = await api<{ entries: AuditEntry[]; next_before_id: number | null }>(
      `/api/v1/system/audit?${query(more ? next.value : null)}`,
    )
    entries.value = more ? [...entries.value, ...r.entries] : r.entries
    next.value = r.next_before_id
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    facets.value = await api('/api/v1/system/audit/facets')
  } catch {
    /* the filters stay free text */
  }
  await load()
})
watch(filters, () => void load())

function clear() {
  Object.assign(filters, { actor: '', action: '', outcome: '', since: '', until: '' })
}
</script>

<template>
  <section class="panel" data-testid="audit" data-tour="audit">
    <div class="panel-head">
      <h2>Audit log</h2>
      <span class="panel-note">who did what · newest first · kept in the database (and in every backup)</span>
    </div>

    <div class="filters" role="group" aria-label="Filter the audit log">
      <label>
        <span>Person</span>
        <select v-model="filters.actor" class="select" data-testid="audit-actor">
          <option value="">Everyone</option>
          <option v-for="a in facets.actors" :key="a" :value="a">{{ a }}</option>
        </select>
      </label>
      <label>
        <span>Action</span>
        <select v-model="filters.action" class="select" data-testid="audit-action">
          <option value="">All actions</option>
          <optgroup label="Areas">
            <option v-for="a in actionGroups.areas" :key="`area-${a}`" :value="a">{{ auditAction(a) }} (all)</option>
          </optgroup>
          <optgroup label="Actions">
            <option v-for="a in actionGroups.actions" :key="a" :value="a">{{ auditAction(a) }}</option>
          </optgroup>
        </select>
      </label>
      <label>
        <span>Result</span>
        <select v-model="filters.outcome" class="select" data-testid="audit-outcome">
          <option value="">Any</option>
          <option value="failed">Failed or refused</option>
        </select>
      </label>
      <label>
        <span>From</span>
        <input v-model="filters.since" class="input" type="date" data-testid="audit-since" />
      </label>
      <label>
        <span>To</span>
        <input v-model="filters.until" class="input" type="date" data-testid="audit-until" />
      </label>
      <button type="button" class="btn btn-sm clear" @click="clear">Clear filters</button>
    </div>

    <p v-if="error" class="err" role="alert">{{ error }}</p>
    <div class="table-wrap">
      <table class="data">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Result</th><th>Details</th></tr></thead>
        <tbody>
          <template v-for="e in entries" :key="e.id">
            <tr class="selectable" :data-testid="`audit-${e.id}`" tabindex="0" @click="open = open === e.id ? null : e.id"
              @keydown.enter="open = open === e.id ? null : e.id">
              <td class="num">{{ dateTime(e.created_at) }}</td>
              <td>{{ e.actor }}</td>
              <td :title="e.subject">{{ auditAction(e.subject) }}</td>
              <td><StatusPill :tone="auditTone(e.outcome)" :label="e.outcome" /></td>
              <td class="detail">{{ auditDetail(e) }}</td>
            </tr>
            <tr v-if="open === e.id" class="raw">
              <td colspan="5"><pre>{{ e.subject }}
{{ JSON.stringify(e.payload, null, 2) }}</pre></td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-if="!loading && !entries.length && !error" class="empty">Nothing matches these filters.</p>
    </div>
    <button v-if="next" type="button" class="btn btn-sm more" :disabled="loading" data-testid="audit-more" @click="load(true)">Load older</button>
  </section>
</template>

<style scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
  align-items: flex-end;
  margin-bottom: 12px;
}
.filters label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.filters .select,
.filters .input {
  min-width: 150px;
}
.clear {
  margin-inline-start: auto;
}
.detail {
  font-size: 12px;
  color: var(--muted);
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.raw pre {
  margin: 0;
  font: 12px/1.5 var(--font-mono);
  white-space: pre-wrap;
  word-break: break-word;
}
.err {
  color: var(--danger);
}
.more {
  margin-top: 10px;
}
</style>
