<script setup lang="ts">
// Finance > Documents (W2-B): every payment's receipt / tax invoice-receipt and every refund's credit document from
// the invoicing provider (Morning), with their numbers and PDFs; failed ones to issue again; payments that have no
// document yet. Words from the i18n catalogue.
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/ui/EmptyState.vue'
import { api, command, errorText } from '@/lib/api'
import { dateTime, shekels } from '@/lib/format'
import type { DocStatus } from '@/lib/order-types'
import { useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { useToasts } from '@/stores/toasts'

interface Doc {
  id: number
  connector_id: string
  payment_id: string
  order_id: string | null
  kind: 'receipt' | 'invoice_receipt' | 'credit'
  amount_agorot: number
  status: DocStatus
  number: string | null
  url: string | null
  allocation: string | null
  attempts: number
  error: string | null
  created_at: string
  issued_at: string | null
  mode: 'fake' | 'live'
}
interface Missing { payment_id: string; order_id: string | null; kind: 'payment' | 'refund'; amount_agorot: number; method: string; at: string }
interface Provider { connector_id: string; label: string; enabled: boolean; mode: string }

const { t } = useI18n()
const auth = useAuth()
const toasts = useToasts()
const live = useLive()
const docs = ref<Doc[]>([])
const missing = ref<Missing[]>([])
const providers = ref<Provider[]>([])
const loaded = ref(false)
const error = ref('')

async function load() {
  try {
    const r = await api<{ documents: Doc[]; without_document: Missing[]; providers: Provider[] }>('/api/v1/documents')
    docs.value = r.documents
    missing.value = r.without_document
    providers.value = r.providers
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loaded.value = true
  }
}
onMounted(load)
let timer: ReturnType<typeof setTimeout> | null = null
let seen = new Set(live.feed.map((e) => e.key))
live.$subscribe(() => {
  const fresh = live.feed.filter((e) => !seen.has(e.key))
  seen = new Set(live.feed.map((e) => e.key))
  if (fresh.some((e) => e.d.kind.startsWith('integrations.document.') || e.d.kind.startsWith('order.payment.'))) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), 500)
  }
})

const on = computed(() => providers.value.find((p) => p.enabled) ?? null)
const SHOW = 5
const allMissing = ref(false)
const shownMissing = computed(() => (allMissing.value ? missing.value : missing.value.slice(0, SHOW)))

async function issue(paymentId: string) {
  try {
    await command('documents.issue', { payment_id: paymentId })
    toasts.info(t('documents.asked'), t('documents.askedText'))
    setTimeout(() => void load(), 1500)
  } catch (err) {
    toasts.error(paymentId, errorText(err))
  }
}
</script>

<template>
  <section class="panel" data-testid="documents" data-tour="finance-documents">
    <div class="panel-head"><h2>{{ t('documents.title') }}</h2><span class="panel-note">{{ t('documents.subtitle') }}</span></div>
    <p v-if="error" class="err" role="alert">{{ error }}</p>
    <p v-if="loaded && !on" class="muted small" data-testid="documents-off">{{ t('documents.off') }}
      <RouterLink v-if="auth.can('owner')" to="/integrations">{{ t('documents.connect') }}</RouterLink></p>
    <div v-if="docs.length" class="table-wrap">
      <table class="data compact">
        <thead><tr><th>{{ t('documents.cols.document') }}</th><th>{{ t('documents.cols.order') }}</th><th class="num">{{ t('documents.cols.amount') }}</th><th>{{ t('documents.cols.status') }}</th><th>{{ t('documents.cols.when') }}</th><th /></tr></thead>
        <tbody>
          <tr v-for="d in docs" :key="d.id" :data-testid="`document-${d.payment_id}`">
            <td>
              {{ t(`documents.kinds.${d.kind}`) }}
              <a v-if="d.url && d.status === 'issued'" :href="d.url" target="_blank" rel="noopener noreferrer" class="mono">{{ d.number }}</a>
              <span v-if="d.mode === 'fake' && d.status === 'issued'" class="muted small"> ({{ t('documents.testDoc') }})</span>
              <div v-if="d.allocation" class="muted small">{{ t('documents.allocation', { n: d.allocation }) }}</div>
            </td>
            <td><RouterLink v-if="d.order_id" :to="`/orders?order=${d.order_id}`">{{ d.order_id }}</RouterLink><span v-else class="muted">—</span></td>
            <td class="num">{{ d.kind === 'credit' ? '−' : '' }}{{ shekels(d.amount_agorot) }}</td>
            <td>
              <span class="st" :class="d.status">{{ t(`documents.status.${d.status}`) }}</span>
              <div v-if="d.error" class="err small">{{ d.error }}<template v-if="d.attempts > 1"> · {{ t('documents.tries', { n: d.attempts }) }}</template></div>
            </td>
            <td class="small">{{ dateTime(d.issued_at ?? d.created_at) }}</td>
            <td><button v-if="d.status === 'failed'" type="button" class="btn btn-sm" :disabled="!auth.can('operator')" @click="issue(d.payment_id)">{{ t('documents.issueAgain') }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-else-if="loaded" icon="inbox" :title="t('documents.none')" />
  </section>

  <section v-if="missing.length" class="panel" data-testid="without-document">
    <div class="panel-head"><h2>{{ t('documents.without') }}</h2><span class="panel-note">{{ t('documents.withoutNote') }}</span></div>
    <ul class="list">
      <li v-for="p in shownMissing" :key="p.payment_id">
        <span>{{ dateTime(p.at) }} · <b class="mono">{{ p.kind === 'refund' ? '−' : '' }}{{ shekels(p.amount_agorot) }}</b> · {{ p.method }}
          <RouterLink v-if="p.order_id" :to="`/orders?order=${p.order_id}`">{{ p.order_id }}</RouterLink></span>
        <button v-if="on" type="button" class="btn btn-sm" :disabled="!auth.can('operator')" :data-testid="`issue-${p.payment_id}`" @click="issue(p.payment_id)">{{ t('documents.issue') }}</button>
      </li>
    </ul>
    <button v-if="missing.length > SHOW" type="button" class="btn btn-sm more" data-testid="missing-toggle" @click="allMissing = !allMissing">
      {{ allMissing ? t('documents.showFewer') : t('documents.showAll', { n: missing.length }) }}
    </button>
  </section>
</template>

<style scoped>
.st {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}
.st.issued {
  color: var(--ok);
}
.st.failed,
.err {
  color: var(--danger-text);
}
.st.manual {
  color: var(--warn);
}
.more {
  margin-top: 8px;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.list li {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  padding: 6px 0;
  border-top: 1px solid var(--border);
}
.list li:first-child {
  border-top: 0;
}
</style>
