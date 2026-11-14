<script setup lang="ts">
// One order: the actions its stage allows (the same moves as dragging on the board, plus QC, ship, cancel and
// payments), line items with prices, every unit's progress, payments and the stage history.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { command, errorText } from '@/lib/api'
import { stageLabel } from '@/lib/events'
import { dateTime, shekels } from '@/lib/format'
import type { OrderDetail, OrderState } from '@/lib/order-types'
import { PAYMENT_METHODS, toAgorot } from '@/lib/orders'
import type { Tone } from '@/lib/types'
import { useConfirm } from '@/stores/confirm'
import { useLive } from '@/stores/live'
import { useOrders } from '@/stores/orders'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ orderId: string }>()
const emit = defineEmits<{ close: [] }>()

const orders = useOrders()
const live = useLive()
const confirm = useConfirm()
const toasts = useToasts()
const order = ref<OrderDetail | null>(null)
const error = ref('')
const busy = ref(false)
const texts = reactive<Record<string, string>>({})
const failUnits = ref<string[]>([])
const qcReason = ref('')
const ship = reactive({ carrier: '', tracking: '' })
const pay = reactive({ amount: '', method: 'bit' })

const TONE: Record<OrderState, Tone> = {
  LEAD: 'idle', RECEIVED: 'accent', PERSONALIZING: 'accent', SCHEDULING: 'accent', PRINTING: 'ok', QC: 'warn',
  SHIPPED: 'ok', NEEDS_ATTENTION: 'danger', CANCELLED: 'idle',
}

async function load() {
  try {
    order.value = await orders.detail(props.orderId)
    error.value = ''
    for (const i of order.value.items) if (!(i.item_id in texts)) texts[i.item_id] = i.text
    const balance = order.value.total_agorot - order.value.paid_agorot
    if (!pay.amount && balance > 0) pay.amount = (balance / 100).toFixed(2)
  } catch (err) {
    error.value = errorText(err)
  }
}
watch(() => props.orderId, () => void load(), { immediate: true })

let seen = new Set(live.feed.map((e) => e.key))
let reload: ReturnType<typeof setTimeout> | null = null
watch(() => live.feed, (feed) => {
  const fresh = feed.filter((e) => !seen.has(e.key))
  seen = new Set(feed.map((e) => e.key))
  if (fresh.some((e) => e.d.orderId === props.orderId)) {
    if (reload) clearTimeout(reload)
    reload = setTimeout(() => void load(), 300)
  }
})
const onKey = (e: KeyboardEvent) => e.key === 'Escape' && emit('close')
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  if (reload) clearTimeout(reload)
})

const o = computed(() => order.value)
const failedItems = computed(() => o.value?.items.filter((i) => i.model.status === 'failed') ?? [])
const allModelsReady = computed(() => !!o.value && o.value.items.every((i) => i.model.status === 'ready'))
const balance = computed(() => (o.value ? o.value.total_agorot - o.value.paid_agorot : 0))
const canCancel = computed(() => !!o.value && ['LEAD', 'RECEIVED', 'PERSONALIZING', 'SCHEDULING', 'NEEDS_ATTENTION'].includes(o.value.state))

async function run(label: string, fn: () => Promise<unknown>, note = 'The board updates when the coordinator confirms.') {
  busy.value = true
  try {
    await fn()
    toasts.info(label, note)
  } catch (err) {
    toasts.error(`${label} failed`, errorText(err))
  } finally {
    busy.value = false
  }
}

const id = () => props.orderId
const confirmLead = () => run('Order confirmed', () => orders.advance(id(), 'RECEIVED'))
const personalize = () => run('Personalization dispatched', () => orders.advance(id(), 'PERSONALIZING'))
const schedule = () => run('Print jobs dispatched', () => orders.advance(id(), 'SCHEDULING'))
const retryUnits = () => run('Failed units dispatched again', () => orders.advance(id(), 'SCHEDULING'))
function retryTexts() {
  const changed = Object.fromEntries(failedItems.value.map((i) => [i.item_id, texts[i.item_id] ?? i.text]))
  return run('Personalization retried', () => orders.advance(id(), 'PERSONALIZING', changed))
}
const passQc = () => run('QC passed', () => orders.qc(id(), true, [], qcReason.value))
async function failQc() {
  const units = failUnits.value
  const ok = await confirm.ask({
    title: `Fail QC for order ${id()}?`,
    text: `${units.length ? `Units ${units.join(', ')}` : 'All units'} are printed again${qcReason.value ? ` (${qcReason.value})` : ''}. After 3 failed checks the order needs attention.`,
    confirmLabel: 'Fail and reprint',
    danger: true,
  })
  if (ok) await run('QC failed: reprinting', () => orders.qc(id(), false, units, qcReason.value))
  failUnits.value = []
}
const doShip = () => run('Order shipped', () => orders.ship(id(), ship.carrier.trim(), ship.tracking.trim()))
async function cancel() {
  const ok = await confirm.ask({
    title: `Cancel order ${id()}?`,
    text: 'Queued print jobs are dropped. Payments are not refunded automatically: record a refund in Finance if needed.',
    confirmLabel: 'Cancel order',
    danger: true,
  })
  if (ok) await run('Order cancelled', () => orders.cancel(id(), 'cancelled from the dashboard'))
}
async function recordPayment() {
  const amount = toAgorot(pay.amount)
  if (!(amount > 0)) return toasts.error('Payment not recorded', 'Enter an amount above zero.')
  await run('Payment recorded', () => command('payments.record', { order_id: id(), amount_agorot: amount, method: pay.method }),
    'Finance books it as income with its VAT split.')
  pay.amount = ''
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <aside class="drawer" role="dialog" aria-modal="true" :aria-label="`Order ${orderId}`" data-testid="order-drawer">
      <header class="head">
        <div class="title">
          <h2>#{{ orderId.replace(/^ord-/, '') }}</h2>
          <StatusPill v-if="o" :tone="TONE[o.state]" :label="stageLabel(o.state)" />
          <span v-if="o" class="chip">{{ o.channel }}</span>
        </div>
        <button type="button" class="icon-btn" aria-label="Close order" @click="emit('close')"><Icon name="x" /></button>
      </header>
      <p v-if="error" class="err" role="alert">{{ error }}</p>

      <template v-if="o">
        <p class="who">
          <b>{{ o.customer_name }}</b><span v-if="o.customer_ref" class="muted"> · {{ o.customer_ref }}</span>
          · due {{ o.due_by ? dateTime(o.due_by) : '—' }} · created {{ dateTime(o.created_at) }} by {{ o.created_by }}
        </p>
        <p class="status" :class="{ bad: o.state === 'NEEDS_ATTENTION' }">{{ o.status }}</p>
        <p v-if="o.notes" class="notes">{{ o.notes }}</p>

        <!-- ------------------------------------------------ what can be done now -->
        <section v-if="!['SHIPPED', 'CANCELLED'].includes(o.state)" class="actions" data-testid="order-actions">
          <template v-if="o.state === 'LEAD'">
            <button type="button" class="btn btn-primary" :disabled="busy" @click="confirmLead">Confirm order</button>
          </template>
          <template v-else-if="o.state === 'RECEIVED'">
            <button type="button" class="btn btn-primary" :disabled="busy" @click="personalize">Start personalization</button>
          </template>
          <template v-else-if="o.state === 'PERSONALIZING'">
            <button type="button" class="btn btn-primary" :disabled="busy || !allModelsReady" @click="schedule">Send to printing</button>
            <span v-if="!allModelsReady" class="muted small">Waiting for every nameplate model.</span>
          </template>
          <template v-else-if="o.state === 'NEEDS_ATTENTION' && o.attention?.from === 'PERSONALIZING'">
            <div class="retry">
              <span class="muted small">Fix the text of the failed nameplates, then retry:</span>
              <label v-for="i in failedItems" :key="i.item_id">
                Item {{ i.item_id }} · {{ i.model.error }}
                <input v-model="texts[i.item_id]" class="input" maxlength="40" dir="auto" :data-testid="`retry-text-${i.item_id}`" />
              </label>
              <button type="button" class="btn btn-primary" :disabled="busy" @click="retryTexts">Retry personalization</button>
            </div>
          </template>
          <template v-else-if="o.state === 'NEEDS_ATTENTION'">
            <button type="button" class="btn btn-primary" :disabled="busy" @click="retryUnits">Print failed units again</button>
          </template>
          <template v-else-if="o.state === 'QC'">
            <div v-if="o.qc !== 'passed'" class="qc">
              <label>Reason (for a failure)<input v-model="qcReason" class="input" maxlength="300" /></label>
              <fieldset>
                <legend class="small muted">Failed units (none ticked = all)</legend>
                <label v-for="u in o.unit_status" :key="u.unit" class="unit">
                  <input v-model="failUnits" type="checkbox" :value="u.unit" /> {{ u.unit }} · {{ u.item_text }}
                </label>
              </fieldset>
              <div class="row">
                <button type="button" class="btn btn-primary" :disabled="busy" data-testid="qc-pass" @click="passQc"><Icon name="check" :size="14" />Pass</button>
                <button type="button" class="btn btn-danger" :disabled="busy" data-testid="qc-fail" @click="failQc">Fail · reprint</button>
              </div>
            </div>
            <div v-else class="ship">
              <label>Carrier<input v-model="ship.carrier" class="input" maxlength="60" placeholder="Israel Post, pickup, …" /></label>
              <label>Tracking<input v-model="ship.tracking" class="input" maxlength="80" /></label>
              <button type="button" class="btn btn-primary" :disabled="busy" data-testid="ship" @click="doShip">Mark shipped</button>
            </div>
          </template>
          <span v-else-if="o.state === 'SCHEDULING' || o.state === 'PRINTING'" class="muted small">Printers drive this stage.</span>
          <button v-if="canCancel" type="button" class="btn btn-sm cancel" :disabled="busy" @click="cancel">Cancel order</button>
        </section>

        <!-- ------------------------------------------------ items and prices -->
        <h3>Items</h3>
        <div class="table-wrap">
          <table class="data compact">
            <thead><tr><th>#</th><th>Item</th><th>Color</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Total</th><th>Model</th></tr></thead>
            <tbody>
              <tr v-for="(i, n) in o.items" :key="i.item_id">
                <td class="num">{{ i.item_id }}</td>
                <td>{{ i.label }}<span v-if="i.text" class="muted"> “<bdi>{{ i.text }}</bdi>”</span></td>
                <td>{{ i.material }} {{ i.color }}</td>
                <td class="num">{{ i.quantity }}</td>
                <td class="num">{{ shekels(o.pricing.lines[n]?.unit_price_agorot) }}</td>
                <td class="num">{{ shekels(o.pricing.lines[n]?.line_total_agorot) }}</td>
                <td>
                  <StatusPill
                    :tone="i.model.status === 'ready' ? 'ok' : i.model.status === 'failed' ? 'danger' : i.model.status === 'generating' ? 'accent' : 'idle'"
                    :label="i.model.status ?? '—'"
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <dl class="sum">
          <dt>Items</dt><dd class="mono">{{ shekels(o.pricing.subtotal_agorot) }}</dd>
          <dt>Discount</dt><dd class="mono">−{{ shekels(o.pricing.discount_agorot) }}</dd>
          <dt>Shipping</dt><dd class="mono">{{ shekels(o.pricing.shipping_agorot) }}</dd>
          <dt class="grand">Total</dt><dd class="mono grand">{{ shekels(o.total_agorot) }}</dd>
          <dt>Paid</dt><dd class="mono">{{ shekels(o.paid_agorot) }}</dd>
          <dt>Balance</dt><dd class="mono" :class="balance > 0 ? 'text-warn' : 'text-ok'">{{ shekels(balance) }}</dd>
        </dl>

        <!-- ------------------------------------------------ payments -->
        <h3>Payments</h3>
        <ul class="list">
          <li v-for="p in o.payments" :key="p.payment_id">
            {{ dateTime(p.at) }} · <b class="mono">{{ p.kind === 'refund' ? '−' : '' }}{{ shekels(p.amount_agorot) }}</b> · {{ p.method }} · {{ p.recorded_by }}
          </li>
          <li v-if="!o.payments.length" class="muted">No payment recorded yet.</li>
        </ul>
        <form v-if="o.state !== 'CANCELLED'" class="pay" @submit.prevent="recordPayment">
          <label>Amount ₪<input v-model="pay.amount" class="input" inputmode="decimal" data-testid="pay-amount" /></label>
          <label>
            Method
            <select v-model="pay.method" class="select">
              <option v-for="m in PAYMENT_METHODS" :key="m" :value="m">{{ m.replace('_', ' ') }}</option>
            </select>
          </label>
          <button type="submit" class="btn" :disabled="busy">Record payment</button>
        </form>

        <!-- ------------------------------------------------ units, QC and history -->
        <h3>Units</h3>
        <div class="units">
          <span v-for="u in o.unit_status" :key="u.unit" class="unit-chip" :class="u.status" :title="`${u.item_text} · attempt ${u.attempt}`">
            {{ u.unit }} · {{ u.status }}<span v-if="u.attempt > 1"> · try {{ u.attempt }}</span>
          </span>
        </div>
        <template v-if="o.qc_checks.length">
          <h3>Quality checks</h3>
          <ul class="list">
            <li v-for="(q, n) in o.qc_checks" :key="n">
              {{ dateTime(q.checked_at) }} · {{ q.passed ? 'passed' : `failed ${q.units_failed}/${q.units_checked} units` }}{{ q.reason ? ` · ${q.reason}` : '' }} · {{ q.checked_by }}
            </li>
          </ul>
        </template>
        <h3>History</h3>
        <ol class="history">
          <li v-for="(h, n) in o.history" :key="n">
            <span class="mono muted">{{ dateTime(h.at) }}</span> {{ stageLabel(h.from_state) }} → <b>{{ stageLabel(h.to_state) }}</b>
          </li>
        </ol>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 40;
  display: flex;
  justify-content: flex-end;
}
.drawer {
  width: min(680px, 100vw);
  height: 100%;
  overflow-y: auto;
  background: var(--surface);
  border-left: 1px solid var(--border);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
/* In a scrolling flex column, a child that scrolls itself (the tables' wrappers) would shrink and clip its rows. */
.drawer > * {
  flex-shrink: 0;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.title {
  display: flex;
  gap: 8px;
  align-items: center;
}
.title h2 {
  font: 700 20px var(--font-display);
}
.who,
.notes {
  font-size: 13px;
}
.status {
  color: var(--accent);
  font-size: 13px;
}
.status.bad,
.err {
  color: var(--danger);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
}
.actions label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.retry,
.qc,
.ship {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1 1 100%;
}
.qc fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}
.qc .unit {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  color: var(--text);
}
.row {
  display: flex;
  gap: 8px;
}
.cancel {
  margin-left: auto;
}
.small {
  font-size: 12px;
}
h3 {
  font: 600 13px var(--font);
  margin-top: 4px;
}
.compact {
  min-width: 0;
}
.compact td {
  white-space: nowrap;
}
.sum {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 16px;
  margin: 0 0 0 auto;
  min-width: 240px;
  font-size: 13px;
}
.sum dt {
  color: var(--muted);
}
.sum dd {
  margin: 0;
  text-align: right;
}
.grand {
  font-weight: 700;
  color: var(--text);
}
.list,
.history {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pay {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.pay label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.units {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.unit-chip {
  font: 500 12px var(--font-mono);
  padding: 2px 8px;
  border-radius: var(--radius-xs);
  background: var(--surface-2);
  color: var(--muted);
}
.unit-chip.printed {
  color: var(--ok);
}
.unit-chip.printing,
.unit-chip.queued {
  color: var(--accent);
}
.unit-chip.failed {
  color: var(--danger);
}
</style>
