<script setup lang="ts">
// Record a payment (an order's, or income without an order such as a market stall) or add an expense. Both are
// commands; the finance agent books them with their VAT split.
import { computed, reactive, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { CATEGORY_LABEL, EXPENSE_CATEGORIES, type Receivable } from '@/lib/finance'
import { shekels } from '@/lib/format'
import { PAYMENT_METHODS, toAgorot } from '@/lib/orders'
import { useFinance } from '@/stores/finance'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ mode: 'payment' | 'expense'; receivables?: Receivable[]; orderId?: string | null }>()
const emit = defineEmits<{ close: [] }>()
const finance = useFinance()
const toasts = useToasts()
const busy = ref(false)
const error = ref('')
const today = new Date().toISOString().slice(0, 10)

const pay = reactive({ order: props.orderId ?? '', amount: '', method: 'bit', receipt: '', date: today })
const exp = reactive({ category: 'shipping', amount: '', vatInvoice: true, estimated: false, vendor: '', date: today, note: '' })
const due = computed(() => props.receivables?.find((r) => r.order_id === pay.order)?.due_agorot ?? null)
if (due.value) pay.amount = (due.value / 100).toFixed(2)

function pickOrder() {
  if (due.value) pay.amount = (due.value / 100).toFixed(2)
}

async function submit() {
  const amount = toAgorot(props.mode === 'payment' ? pay.amount : exp.amount)
  if (!(amount > 0)) return (error.value = 'enter an amount above zero')
  busy.value = true
  error.value = ''
  try {
    if (props.mode === 'payment') {
      await finance.recordPayment({
        ...(pay.order ? { order_id: pay.order } : {}), amount_agorot: amount, method: pay.method,
        ...(pay.receipt.trim() ? { receipt_no: pay.receipt.trim() } : {}), paid_at: new Date(`${pay.date}T12:00:00`).toISOString(),
      })
      toasts.ok('Payment recorded', `${shekels(amount)} · booked as income with its VAT split`)
    } else {
      await finance.addExpense({
        category: exp.category, gross_agorot: amount, vat_invoice: exp.vatInvoice, estimated: exp.estimated,
        ...(exp.vendor.trim() ? { vendor: exp.vendor.trim() } : {}), date: new Date(`${exp.date}T12:00:00`).toISOString(),
        ...(exp.note.trim() ? { note: exp.note.trim() } : {}),
      })
      toasts.ok('Expense added', `${shekels(amount)} · ${CATEGORY_LABEL[exp.category]}`)
    }
    finance.reloadSoon()
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <form class="dialog" role="dialog" aria-modal="true" :aria-label="mode === 'payment' ? 'Record payment' : 'Add expense'" :data-testid="`${mode}-dialog`" @submit.prevent="submit">
      <header>
        <h2>{{ mode === 'payment' ? 'Record payment' : 'Add expense' }}</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </header>
      <div v-if="mode === 'payment'" class="grid">
        <label class="wide">
          Order
          <select v-model="pay.order" class="select" @change="pickOrder">
            <option value="">No order (market stall, other income)</option>
            <option v-for="r in receivables" :key="r.order_id" :value="r.order_id">#{{ r.order_id.replace(/^ord-/, '') }} · {{ r.customer_name }} · owes {{ shekels(r.due_agorot) }}</option>
          </select>
        </label>
        <label>Amount ₪ (incl. VAT)<input v-model="pay.amount" class="input" inputmode="decimal" data-testid="money-amount" /></label>
        <label>
          Method
          <select v-model="pay.method" class="select">
            <option v-for="m in PAYMENT_METHODS" :key="m" :value="m">{{ m.replace('_', ' ') }}</option>
          </select>
        </label>
        <label>Date<input v-model="pay.date" class="input" type="date" /></label>
        <label>Receipt no.<input v-model="pay.receipt" class="input" maxlength="64" placeholder="from your invoicing system" /></label>
      </div>
      <div v-else class="grid">
        <label>
          Category
          <select v-model="exp.category" class="select" data-testid="expense-category">
            <option v-for="c in EXPENSE_CATEGORIES" :key="c" :value="c">{{ CATEGORY_LABEL[c] }}</option>
          </select>
        </label>
        <label>Amount ₪ (incl. VAT)<input v-model="exp.amount" class="input" inputmode="decimal" data-testid="money-amount" /></label>
        <label>Vendor<input v-model="exp.vendor" class="input" maxlength="120" /></label>
        <label>Date<input v-model="exp.date" class="input" type="date" /></label>
        <label class="wide">Note<input v-model="exp.note" class="input" maxlength="500" /></label>
        <label class="check"><input v-model="exp.vatInvoice" type="checkbox" /> Tax invoice (its VAT is reclaimed)</label>
        <label class="check"><input v-model="exp.estimated" type="checkbox" /> Estimate until the real bill arrives</label>
        <p v-if="exp.category === 'electricity'" class="wide hint">
          An electricity bill replaces the estimate from print hours for its period: date it in the month it covers.
        </p>
      </div>
      <p v-if="error" class="err" role="alert">{{ error }}</p>
      <footer>
        <button type="button" class="btn" @click="emit('close')">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="busy">{{ mode === 'payment' ? 'Record payment' : 'Add expense' }}</button>
      </footer>
    </form>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  z-index: 45;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  overflow-y: auto;
  padding: 60px 16px;
}
.dialog {
  width: min(560px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
header,
footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
footer {
  justify-content: flex-end;
}
h2 {
  font: 700 17px var(--font-display);
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.wide {
  grid-column: span 2;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.check {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  color: var(--text);
  grid-column: span 2;
}
.hint {
  font-size: 12px;
  color: var(--muted);
}
.err {
  color: var(--danger);
  font-size: 13px;
}
</style>
