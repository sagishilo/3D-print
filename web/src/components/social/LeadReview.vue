<script setup lang="ts">
// The review queue (design screen 1): the original post, the matched SKU, the AI draft (editable), and the three
// steps a person takes: Approve & Copy -> paste it in the group -> Mark as posted. Nothing is ever posted for them.
// Keys (outside the text box): J / K move, A approve & copy, P mark posted, R reject.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { ago, shekels } from '@/lib/format'
import { copyText, LEAD_LABEL, type Lead, replyText, score } from '@/lib/social'
import { useAuth } from '@/stores/auth'
import { useSocial } from '@/stores/social'
import { useToasts } from '@/stores/toasts'
import { vCan } from '@/directives/can'

const props = defineProps<{ leads: Lead[]; draftModel: string }>()
const auth = useAuth()
const social = useSocial()
const toasts = useToasts()

const index = ref(0)
const edits = ref<Record<string, string>>({})
const busy = ref(false)
const lead = computed<Lead | null>(() => props.leads[Math.min(index.value, props.leads.length - 1)] ?? null)
const text = computed({
  get: () => (lead.value ? (edits.value[lead.value.lead_id] ?? lead.value.final_text ?? lead.value.draft) : ''),
  set: (v: string) => {
    if (lead.value) edits.value[lead.value.lead_id] = v
  },
})
const open = computed(() => lead.value && (lead.value.status === 'pending' || lead.value.status === 'approved'))
watch(() => props.leads.length, (n) => {
  if (index.value >= n) index.value = Math.max(0, n - 1)
})

async function act(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  try {
    await fn()
  } catch (err) {
    toasts.error('Not saved', errorText(err))
  } finally {
    busy.value = false
  }
}

const approveAndCopy = () => act(async () => {
  const l = lead.value
  if (!l || !open.value) return
  const reply = replyText(l, edits.value[l.lead_id])
  if (!reply) {
    toasts.error('Nothing to copy', 'Write the reply first.')
    return
  }
  const copied = await copyText(reply)
  await social.decide(l, true, reply)
  if (copied) toasts.ok('Approved and copied', 'Paste it in the group, post it, then Mark as posted.')
  else toasts.info('Approved', 'Copying was blocked by the browser: select the text and copy it yourself.')
})

const markPosted = () => act(async () => {
  const l = lead.value
  if (!l || l.status !== 'approved') return
  await social.markPosted(l)
  toasts.ok('Marked as posted', `${l.group_name || 'post'} · thank you`)
})

const reject = () => act(async () => {
  const l = lead.value
  if (!l || !open.value) return
  await social.decide(l, false)
})

function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.isContentEditable)) return
  if (e.metaKey || e.ctrlKey || e.altKey) return
  const k = e.key.toLowerCase()
  if (['a', 'p', 'r'].includes(k) && !auth.can('operator')) return   // viewers only read
  if (k === 'j') index.value = Math.min(index.value + 1, props.leads.length - 1)
  else if (k === 'k') index.value = Math.max(index.value - 1, 0)
  else if (k === 'a') void approveAndCopy()
  else if (k === 'p') void markPosted()
  else if (k === 'r') void reject()
  else return
  e.preventDefault()
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <section class="panel review" aria-label="Review queue" data-testid="review">
    <div class="panel-head">
      <h2>Review queue · {{ leads.filter((l) => l.status === 'pending').length }} draft{{ leads.filter((l) => l.status === 'pending').length === 1 ? '' : 's' }}</h2>
      <span class="panel-note keys">J / K move · A approve &amp; copy · P mark posted · R reject</span>
    </div>

    <p v-if="!lead" class="empty">No leads to review. Captured posts with buying intent appear here with a draft reply.</p>
    <template v-else>
      <nav class="tabs" aria-label="Leads">
        <button v-for="(l, i) in leads" :key="l.lead_id" type="button" class="tab" :class="{ on: i === index, [l.status]: true }"
          :aria-current="i === index" :title="l.summary" @click="index = i">{{ i + 1 }}</button>
      </nav>

      <div class="block">
        <span class="h">Original post</span>
        <span class="muted small">{{ lead.group_name || 'Pasted post' }} · captured by {{ lead.captured_by }} · {{ ago(Date.parse(lead.created_at)) }}</span>
        <p class="post" dir="auto" data-testid="lead-post">{{ lead.post_text }}</p>
        <span class="small">
          intent <b class="mono">{{ score(lead.intent_score) }}</b> · {{ lead.summary }}
        </span>
        <span v-if="lead.sku" class="small">Matched SKU: <b class="mono">{{ lead.sku }}</b>{{ lead.sku_name ? ` · ${lead.sku_name}` : '' }}{{ lead.sku_price_agorot != null ? ` · ${shekels(lead.sku_price_agorot)}` : '' }}</span>
      </div>

      <label class="block">
        <span class="h">AI draft reply · edit before copying</span>
        <textarea v-model="text" class="input reply" dir="rtl" rows="5" maxlength="2000" :disabled="!open" data-testid="lead-draft" />
        <span v-if="lead.status_reason && !lead.draft" class="small text-warn">{{ lead.status_reason }}</span>
        <span v-if="lead.draft_notes" class="small text-warn">Check: {{ lead.draft_notes }}</span>
      </label>

      <ol class="steps">
        <li :class="{ done: lead.status === 'approved' || lead.status === 'posted' }"><b>1</b> Approve &amp; Copy</li>
        <li :class="{ done: lead.status === 'posted' }"><b>2</b> Paste in the group</li>
        <li :class="{ done: lead.status === 'posted' }"><b>3</b> Mark as posted</li>
      </ol>

      <div class="actions">
        <button v-can="'operator'" type="button" class="btn btn-primary" :disabled="!open || busy" data-testid="approve-copy" @click="approveAndCopy">
          <Icon name="check" :size="14" />Approve &amp; Copy
        </button>
        <a v-if="lead.post_url" class="btn" :href="lead.post_url" target="_blank" rel="noopener noreferrer">Open post <Icon name="external" :size="12" /></a>
        <button v-can="'operator'" type="button" class="btn" :disabled="lead.status !== 'approved' || busy" data-testid="mark-posted" @click="markPosted">Mark as posted</button>
        <span class="spacer" />
        <button v-can="'operator'" type="button" class="btn btn-danger" :disabled="!open || busy" data-testid="reject" @click="reject">Reject</button>
      </div>
      <p class="panel-note">
        {{ LEAD_LABEL[lead.status] }} · drafted by Social Listening ({{ draftModel }}) · nothing is posted until you paste it
      </p>
    </template>
  </section>
</template>

<style scoped>
.review {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.keys {
  font-family: var(--font-mono);
}
.tabs {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.tab {
  min-width: 30px;
  height: 28px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  font: 500 12px var(--font-mono);
  cursor: pointer;
}
.tab.on {
  border-color: var(--accent);
  color: var(--text);
}
.tab.posted,
.tab.dismissed {
  opacity: 0.5;
}
.block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.h {
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.post {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.5;
}
.reply {
  font-size: 14px;
  line-height: 1.5;
  resize: vertical;
}
.small {
  font-size: 12px;
}
.steps {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  list-style: none;
  padding: 0;
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
.steps b {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  margin-inline-end: 4px;
  border-radius: 50%;
  border: 1px solid var(--border);
  font: 600 11px var(--font-mono);
}
.steps .done {
  color: var(--ok);
}
.steps .done b {
  border-color: var(--ok);
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.spacer {
  flex: 1;
}
</style>
