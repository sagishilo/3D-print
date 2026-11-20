<script setup lang="ts">
// One order on the board: number, time in stage, items, customer, due date, what it waits for, plates.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { ago, dateTime } from '@/lib/format'
import type { OrderCard } from '@/lib/order-types'
import { itemsSummary } from '@/lib/orders'
import { nicheColor } from '@/lib/niches'
import { useLive } from '@/stores/live'
import { useNiche } from '@/stores/niche'

const props = defineProps<{ card: OrderCard }>()
defineEmits<{ open: [id: string] }>()

const { t } = useI18n()
const live = useLive()
const niche = useNiche()
/** The niches its lines were sold under (a dot each). */
const niches = computed(() => [...new Set(props.card.items.map((i) => i.niche).filter((n): n is string => !!n))])
const overdue = computed(() => !!props.card.due_by && new Date(props.card.due_by).getTime() < live.now && !['SHIPPED', 'CANCELLED'].includes(props.card.state))
const attention = computed(() => props.card.state === 'NEEDS_ATTENTION')
</script>

<template>
  <article
    class="card"
    :class="{ attention, stuck: card.stuck }"
    draggable="true"
    tabindex="0"
    :data-testid="`order-${card.order_id}`"
    data-tour="order-card"
    :aria-label="`Order ${card.order_id}, ${card.customer_name}: ${card.status}`"
    @click="$emit('open', card.order_id)"
    @keydown.enter="$emit('open', card.order_id)"
  >
    <div class="row">
      <span class="id mono">#{{ card.order_id.replace(/^ord-/, '') }}</span>
      <span class="age" :class="{ warn: card.stuck }">{{ ago(new Date(card.stage_since).getTime(), live.now) }}{{ card.stuck ? ' · stuck' : '' }}</span>
    </div>
    <span class="items"><span v-for="n in niches" :key="n" class="niche-dot" :style="{ background: nicheColor(niche.bySlug[n]) }" :title="niche.name(n)" :data-testid="`card-niche-${n}`" />{{ itemsSummary(card) }}</span>
    <span class="who">
      {{ card.customer_name }} ·
      <span :class="{ late: overdue }">due {{ card.due_by ? dateTime(card.due_by) : '—' }}</span>
    </span>
    <span class="status" :class="{ danger: attention }">{{ card.status }}</span>
    <span v-if="card.shipment" class="chip parcel" :class="card.shipment.status" :data-testid="`card-parcel-${card.order_id}`">{{ t(`shipping.status.${card.shipment.status}`) }}</span>
    <span v-for="p in card.plates" :key="p.plate_id" class="chip plate">{{ p.plate_id.replace(/^plate-/, 'P-').slice(0, 10) }} · {{ p.printer_id }}</span>
  </article>
</template>

<style scoped>
.chip.parcel {
  align-self: flex-start;
  font-family: inherit;
  color: var(--accent);
}
.chip.parcel.delivered {
  color: var(--ok);
}
.chip.parcel.returned {
  color: var(--danger-text);
}
.card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface-2);
  cursor: grab;
  font-size: 12px;
}
.card:hover {
  border-color: var(--border-strong);
}
.card.attention {
  border-color: var(--danger);
}
.card.stuck:not(.attention) {
  border-color: var(--warn);
}
.row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.id {
  font-weight: 600;
  font-size: 13px;
}
.age {
  color: var(--muted);
}
.age.warn,
.late {
  color: var(--warn);
}
.niche-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: middle;
}
.items {
  font-size: 13px;
  font-weight: 500;
}
.who {
  color: var(--muted);
}
.status {
  color: var(--accent);
}
.status.danger {
  color: var(--danger);
}
.plate {
  align-self: flex-start;
}
</style>
