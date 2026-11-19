<script setup lang="ts">
// Social Listening & Trends (design screen 1, ADR-0007): posts captured by group members, what the agent made of
// each, and the review queue where a person approves, copies, posts and marks replies. No auto-post option exists.
import { computed, onMounted, ref, watch } from 'vue'

import AddPostDialog from '@/components/social/AddPostDialog.vue'
import CaptureCard from '@/components/social/CaptureCard.vue'
import LeadReview from '@/components/social/LeadReview.vue'
import SocialSettings from '@/components/social/SocialSettings.vue'
import Icon from '@/components/ui/Icon.vue'
import KpiCard from '@/components/ui/KpiCard.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { errorText } from '@/lib/api'
import { ago } from '@/lib/format'
import { type Capture, captureLine, type Label, leadsCsv, modelLine, STATUS_LABEL, usd } from '@/lib/social'
import { useSocial } from '@/stores/social'
import { useToasts } from '@/stores/toasts'
import { vCan } from '@/directives/can'
import { useAuth } from '@/stores/auth'

const auth = useAuth()
const social = useSocial()
const toasts = useToasts()
const adding = ref(false)

onMounted(() => void social.load())
watch([() => social.leadFilter, () => social.captureFilter], () => void social.load())

const s = computed(() => social.stats)
const total7d = computed(() => Object.values(s.value?.captures_7d ?? {}).reduce((a, b) => a + (b ?? 0), 0))
const oldestPending = computed(() => {
  const p = social.pending
  if (!p.length) return 'none waiting'
  const oldest = p.reduce((a, b) => (a.created_at < b.created_at ? a : b))
  return `oldest ${ago(Date.parse(oldest.created_at))}`
})
const kpis = computed(() => [
  { label: 'Posts captured · today', value: String(s.value?.captures_today ?? 0), sub: `${total7d.value} in 7 days` },
  {
    label: 'Leads · 7 days',
    value: String(s.value?.captures_7d.lead ?? 0),
    sub: `intent ≥ ${social.threshold.toFixed(2)}`,
  },
  { label: 'Drafts awaiting review', value: String(social.pending.length), sub: oldestPending.value,
    subTone: social.pending.length ? ('warn' as const) : ('idle' as const) },
  { label: 'Replies posted · 7 days', value: String(s.value?.posted_7d ?? 0), sub: `model spend today ${usd(s.value?.spend_today_usd ?? 0)}` },
])
const FILTERS: (keyof typeof STATUS_LABEL | null)[] = [null, 'lead', 'scored', 'ignored', 'waiting', 'failed']

async function label(c: Capture, v: Label) {
  try {
    await social.label(c, v)
  } catch (err) {
    toasts.error('Label not saved', errorText(err))
  }
}

async function rescore(c: Capture) {
  try {
    await social.rescore(c)
    toasts.info('Scoring again', c.group_name || 'pasted post')
  } catch (err) {
    toasts.error('Not rescored', errorText(err))
  }
}

function exportLeads() {
  const blob = new Blob([leadsCsv(social.leads)], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `leads-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Social Listening &amp; Trends</h1>
      <p data-testid="capture-line">{{ captureLine(s) }}</p>
    </div>
    <div class="head-actions">
      <button type="button" class="btn" :disabled="!social.leads.length" @click="exportLeads"><Icon name="download" :size="14" />Export leads</button>
      <button v-can="'operator'" type="button" class="btn btn-primary" data-testid="add-post-open" @click="adding = true"><Icon name="plus" :size="14" />Add post</button>
    </div>
  </div>

  <p v-if="social.error" class="err" role="alert">{{ social.error }}</p>
  <p v-if="s" class="model-line small" :class="{ 'text-warn': s.agent.llm !== 'claude' }" data-testid="model-line">{{ modelLine(s) }}</p>

  <div class="kpis" data-tour="social-kpis">
    <KpiCard v-for="k in kpis" :key="k.label" :label="k.label" :value="k.value" :sub="k.sub" :sub-tone="k.subTone" />
  </div>

  <div class="layout">
    <section class="panel feed" aria-label="Buying-intent feed" data-tour="capture-feed">
      <div class="panel-head">
        <h2>Buying-intent feed</h2>
        <div class="filters" role="group" aria-label="Show">
          <button v-for="f in FILTERS" :key="f ?? 'all'" type="button" class="chip-btn" :class="{ on: social.captureFilter === f }"
            :aria-pressed="social.captureFilter === f" @click="social.captureFilter = f">{{ f ? STATUS_LABEL[f] : 'all' }}</button>
        </div>
      </div>
      <div class="cards">
        <CaptureCard v-for="c in social.captures" :key="c.capture_id" :capture="c" :threshold="social.threshold"
          @label="label(c, $event)" @rescore="rescore(c)" />
        <EmptyState v-if="social.loaded && !social.captures.length && social.captureFilter" icon="activity" title="Nothing with this status"
          action-label="Show all" @action="social.captureFilter = null" />
        <EmptyState v-else-if="social.loaded && !social.captures.length" icon="activity" title="No posts captured yet"
          text="Group members capture posts with the browser extension (Capture extension, below), or paste one here."
          :action-label="auth.can('operator') ? 'Add post' : undefined" @action="adding = true" />
      </div>
    </section>

    <div class="side">
      <LeadReview :leads="social.leads" :draft-model="s?.models.draft ?? 'claude-opus-5-5'" />
      <section class="panel trend" aria-label="Trend Analyst">
        <div class="panel-head">
          <h2>Trend Analyst insights</h2>
          <span class="chip">phase 10b</span>
        </div>
        <p class="empty">Rising and cooling demand from captured posts and sales, with catalog suggestions, arrives with the Trend Analyst.</p>
      </section>
      <p class="notice"><Icon name="alert" :size="14" />Replies are never posted automatically. You post every reply yourself · no auto-post option exists.</p>
    </div>
  </div>

  <SocialSettings />
  <AddPostDialog v-if="adding" @close="adding = false" />
</template>

<style scoped>
.head-actions {
  display: flex;
  gap: 8px;
}
.err {
  color: var(--danger);
}
.small {
  font-size: 12px;
}
.model-line {
  color: var(--muted);
  margin-top: -6px;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.feed .cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 78vh;
  overflow-y: auto;
}
.filters {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.chip-btn {
  padding: 2px 10px;
  min-height: 26px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  font: 500 12px var(--font);
  cursor: pointer;
}
.chip-btn.on {
  border-color: var(--accent);
  color: var(--text);
  background: var(--accent-bg);
}
.side {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.notice {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 12px;
  color: var(--muted);
}
@media (max-width: 1000px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
