<script setup lang="ts">
// Ship an order (W2-B): with a courier connector (Israel Post: it makes the test label, or, live, takes the tracking
// number you got from Israel Post) or a carrier you type; where it goes (prefilled from the store order, else the
// customer). Once the parcel exists: its label to print, its tracking link and where it is (moved on by the courier's
// tracking, or by hand). Words from the i18n catalogue.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { api, command, errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import type { OrderDetail, Shipment, ShipmentStatus } from '@/lib/order-types'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { useToasts } from '@/stores/toasts'

interface Courier { connector_id: string; label: string; enabled: boolean; mode: 'fake' | 'live'; tracking_pattern: string }

const props = defineProps<{ order: OrderDetail; qcPassed: boolean }>()
const emit = defineEmits<{ shipped: [] }>()
const { t } = useI18n()
const auth = useAuth()
const confirm = useConfirm()
const toasts = useToasts()

const couriers = ref<Courier[]>([])
const busy = ref(false)
const OTHER = '__other'
const form = reactive({ carrier: OTHER, other: '', tracking: '', tracking_url: '', name: '', street: '', city: '', zip: '', phone: '', weight: '' })

function prefill() {
  const to = props.order.ship_to ?? {}
  Object.assign(form, { name: to.name ?? props.order.customer_name, street: to.street ?? '', city: to.city ?? '', zip: to.zip ?? '', phone: to.phone ?? '' })
}
onMounted(async () => {
  prefill()
  try {
    const r = await api<{ couriers: Courier[] }>(`/api/v1/shipments?order_id=${encodeURIComponent(props.order.order_id)}&limit=1`)
    couriers.value = r.couriers.filter((c) => c.enabled)
    if (couriers.value.length && form.carrier === OTHER) form.carrier = couriers.value[0].connector_id
  } catch {
    couriers.value = []
  }
})
watch(() => props.order.order_id, prefill)

const parcel = computed<Shipment | null>(() => props.order.shipments?.find((s) => s.status !== 'cancelled') ?? null)
const courier = computed(() => couriers.value.find((c) => c.connector_id === form.carrier) ?? null)
const typedTracking = computed(() => !courier.value || courier.value.mode === 'live')

/** Send the parcel; true when the request was accepted (the order moves to Shipped when the coordinator confirms). */
async function ship(): Promise<boolean> {
  const carrier = courier.value ? courier.value.connector_id : form.other.trim()
  if (!carrier) {
    toasts.error(t('shipping.notShipped'), t('shipping.needCarrier'))
    return false
  }
  if (courier.value?.mode === 'live' && !new RegExp(`^(?:${courier.value.tracking_pattern})$`).test(form.tracking.trim().toUpperCase())) {
    toasts.error(t('shipping.notShipped'), t('shipping.needTracking', { courier: courier.value.label }))
    return false
  }
  const link = form.tracking_url.trim()
  if (!courier.value && link && !link.startsWith('https://')) {
    toasts.error(t('shipping.notShipped'), t('shipping.httpsOnly'))
    return false
  }
  const body: Record<string, unknown> = {
    order_id: props.order.order_id, carrier,
    ship_to: { name: form.name.trim(), street: form.street.trim(), city: form.city.trim(), zip: form.zip.trim(), phone: form.phone.trim() },
  }
  if (typedTracking.value && form.tracking.trim()) body.tracking = form.tracking.trim()
  if (!courier.value && link) body.tracking_url = link
  if (Number(form.weight) > 0) body.weight_g = Math.round(Number(form.weight))
  busy.value = true
  try {
    await command('shipments.create', body)
    toasts.info(t('shipping.asked'), t('shipping.askedText'))
    emit('shipped')
    return true
  } catch (err) {
    toasts.error(t('shipping.notShipped'), errorText(err))
    return false
  } finally {
    busy.value = false
  }
}

async function move(to: ShipmentStatus) {
  if (!parcel.value) return
  if (to === 'cancelled' && !(await confirm.ask({ title: t('shipping.cancelAsk'), text: t('shipping.cancelText'), confirmLabel: t('shipping.cancelLabel'), danger: true }))) return
  try {
    await command('shipments.update', { shipment_id: parcel.value.shipment_id, status: to })
    toasts.info(t(`shipping.status.${to}`), parcel.value.shipment_id)
    emit('shipped')
  } catch (err) {
    toasts.error(parcel.value.shipment_id, errorText(err))
  }
}

defineExpose({ ship })
</script>

<template>
  <div v-if="parcel" class="parcel" data-testid="parcel">
    <div class="head">
      <b>{{ parcel.carrier }}</b><span v-if="parcel.service" class="muted">{{ parcel.service }}</span>
      <span class="st" :class="parcel.status" data-testid="parcel-status">{{ t(`shipping.status.${parcel.status}`) }}</span>
    </div>
    <div v-if="parcel.tracking" class="small">
      {{ t('shipping.tracking') }}:
      <a v-if="parcel.tracking_url" :href="parcel.tracking_url" target="_blank" rel="noopener noreferrer" class="mono" data-testid="parcel-tracking">{{ parcel.tracking }}</a>
      <span v-else class="mono" data-testid="parcel-tracking">{{ parcel.tracking }}</span>
    </div>
    <ol class="events small muted">
      <li v-for="(e, i) in parcel.events" :key="i">{{ dateTime(e.at) }} · {{ t(`shipping.status.${e.status}`) }}<template v-if="e.text"> · {{ e.text }}</template> · {{ e.by }}</li>
    </ol>
    <div class="row">
      <a class="btn btn-sm" :href="parcel.label_url" target="_blank" rel="noopener" data-testid="parcel-label">{{ t('shipping.printLabel') }}</a>
      <template v-if="auth.can('operator')">
        <button v-if="parcel.status === 'label_created'" type="button" class="btn btn-sm" data-testid="parcel-in-transit" @click="move('in_transit')">{{ t('shipping.markInTransit') }}</button>
        <button v-if="parcel.status === 'label_created' || parcel.status === 'in_transit'" type="button" class="btn btn-sm" data-testid="parcel-delivered" @click="move('delivered')">{{ t('shipping.markDelivered') }}</button>
        <button v-if="parcel.status === 'in_transit'" type="button" class="btn btn-sm" @click="move('returned')">{{ t('shipping.markReturned') }}</button>
        <button v-if="parcel.status === 'label_created'" type="button" class="btn btn-sm btn-danger" data-testid="parcel-cancel" @click="move('cancelled')">{{ t('shipping.cancelLabel') }}</button>
      </template>
    </div>
  </div>

  <form v-else class="ship-form" data-testid="ship-form" @submit.prevent="ship">
    <label>{{ t('shipping.courier') }}
      <select v-model="form.carrier" class="select" data-testid="ship-courier">
        <option v-for="c in couriers" :key="c.connector_id" :value="c.connector_id">{{ c.label }}{{ c.mode === 'fake' ? ` (${t('shipping.testMode')})` : '' }}</option>
        <option :value="OTHER">{{ t('shipping.other') }}</option>
      </select>
    </label>
    <label v-if="!courier">{{ t('shipping.carrier') }}<input v-model="form.other" class="input" maxlength="60" :placeholder="t('shipping.carrierHint')" /></label>
    <label v-if="typedTracking">{{ t('shipping.tracking') }}<input v-model="form.tracking" class="input mono" maxlength="80" data-testid="ship-tracking" :placeholder="courier ? 'RR123456789IL' : ''" /></label>
    <label v-if="!courier">{{ t('shipping.trackingLink') }}<input v-model="form.tracking_url" class="input" maxlength="300" placeholder="https://" /></label>
    <p v-if="courier && courier.mode === 'live'" class="muted small hint">{{ t('shipping.liveHint', { courier: courier.label }) }}</p>
    <fieldset class="addr">
      <legend class="small muted">{{ t('shipping.shipTo') }}</legend>
      <input v-model="form.name" class="input" :aria-label="t('shipping.name')" :placeholder="t('shipping.name')" maxlength="80" dir="auto" data-testid="ship-name" />
      <input v-model="form.street" class="input" :aria-label="t('shipping.street')" :placeholder="t('shipping.street')" maxlength="120" dir="auto" data-testid="ship-street" />
      <input v-model="form.city" class="input" :aria-label="t('shipping.city')" :placeholder="t('shipping.city')" maxlength="60" dir="auto" />
      <input v-model="form.zip" class="input" :aria-label="t('shipping.zip')" :placeholder="t('shipping.zip')" maxlength="12" />
      <input v-model="form.phone" class="input" :aria-label="t('shipping.phone')" :placeholder="t('shipping.phone')" maxlength="30" />
      <input v-model="form.weight" class="input" :aria-label="t('shipping.weight')" :placeholder="t('shipping.weight')" inputmode="numeric" />
    </fieldset>
    <button v-if="qcPassed && auth.can('operator')" type="submit" class="btn btn-primary" :disabled="busy" data-testid="ship">{{ t('shipping.ship') }}</button>
  </form>
</template>

<style scoped>
.parcel,
.ship-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ship-form label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.addr {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 8px;
  margin: 0;
}
.head {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.st {
  margin-inline-start: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
}
.st.delivered {
  color: var(--ok);
}
.st.returned {
  color: var(--danger-text);
}
.events {
  margin: 0;
  padding-inline-start: 18px;
}
.row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.hint {
  margin: 0;
}
@media (max-width: 560px) {
  .addr {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
