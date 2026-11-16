<script setup lang="ts">
// One captured post in the buying-intent feed: where it came from, what the agent made of it and why, and the
// person's verdict (Buyer / Not buyer), which joins the labelled set that the prefilter and the accuracy use.
import { computed } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { ago } from '@/lib/format'
import { type Capture, canRescore, intentTone, type Label, score, STATUS_LABEL, STATUS_TONE } from '@/lib/social'

const props = defineProps<{ capture: Capture; threshold: number; selected?: boolean }>()
const emit = defineEmits<{ label: [Label]; rescore: []; open: [] }>()

const c = computed(() => props.capture)
const reason = computed(() => {
  if (c.value.status === 'lead' || !c.value.status_reason) return ''
  return c.value.status_reason
})
</script>

<template>
  <article class="card" :class="{ selected, lead: c.status === 'lead' }" :data-testid="`capture-${c.capture_id}`" data-tour="capture-card">
    <header>
      <span class="src">{{ c.group_name || 'Pasted post' }}</span>
      <span class="muted small">{{ c.captured_by }} · {{ ago(Date.parse(c.received_at)) }}</span>
    </header>
    <p class="text" dir="auto">{{ c.post_text }}</p>
    <p v-if="c.summary" class="summary">{{ c.summary }}</p>
    <footer>
      <StatusPill :tone="STATUS_TONE[c.status]" :label="STATUS_LABEL[c.status]" />
      <span v-if="c.intent_score != null" class="score mono" :class="`text-${intentTone(c.intent_score, threshold)}`"
        :title="c.intent ?? ''">intent {{ score(c.intent_score) }}</span>
      <span v-else-if="c.prefilter != null" class="score mono muted" title="similarity to labelled buyer posts">prefilter {{ score(c.prefilter) }}</span>
      <span v-if="c.sku_guess" class="chip mono">{{ c.sku_guess }}</span>
      <span class="spacer" />
      <div class="label" role="group" aria-label="Your verdict">
        <button type="button" class="btn btn-sm" :class="{ on: c.label === 'buyer' }" :aria-pressed="c.label === 'buyer'"
          :data-testid="`label-buyer-${c.capture_id}`" data-tour="capture-labels" @click="emit('label', 'buyer')">Buyer</button>
        <button type="button" class="btn btn-sm" :class="{ on: c.label === 'not_buyer' }" :aria-pressed="c.label === 'not_buyer'"
          :data-testid="`label-not-${c.capture_id}`" @click="emit('label', 'not_buyer')">Not buyer</button>
      </div>
      <button v-if="canRescore(c)" type="button" class="icon-btn" title="Score again" aria-label="Score again"
        :data-testid="`rescore-${c.capture_id}`" @click="emit('rescore')"><Icon name="refresh" :size="14" /></button>
      <a v-if="c.post_url" class="icon-btn" :href="c.post_url" target="_blank" rel="noopener noreferrer" title="Open post"
        aria-label="Open post"><Icon name="external" :size="14" /></a>
    </footer>
    <p v-if="reason" class="reason small" :class="{ 'text-warn': c.status === 'waiting', 'text-danger': c.status === 'failed' }">{{ reason }}</p>
  </article>
</template>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
}
.card.lead {
  border-left: 3px solid var(--ok);
}
.card.selected {
  border-color: var(--accent);
}
header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
}
.src {
  font-weight: 600;
}
.text {
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.summary {
  font-size: 12px;
  color: var(--muted);
}
footer {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.score {
  font-size: 12px;
}
.spacer {
  flex: 1;
}
.label {
  display: flex;
  gap: 4px;
}
.label .on {
  border-color: var(--accent);
  background: var(--accent-bg);
}
.small {
  font-size: 12px;
}
.reason {
  color: var(--muted);
}
</style>
