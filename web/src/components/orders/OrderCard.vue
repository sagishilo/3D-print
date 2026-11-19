<script setup lang="ts">
// One order on the board: number, time in stage, items, customer, due date, what it waits for, plates.
import { computed } from 'vue'

import { ago, dateTime } from '@/lib/format'
import type { OrderCard } from '@/lib/order-types'
import { itemsSummary } from '@/lib/orders'
import { useLive } from '@/stores/live'

const props = defineProps<{ card: OrderCard }>()
defineEmits<{ open: [id: string] }>()

const live = useLive()
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
    <span class="items">{{ itemsSummary(card) }}</span>
    <span class="who">
      {{ card.customer_name }} ·
      <span :class="{ late: overdue }">due {{ card.due_by ? dateTime(card.due_by) : '—' }}</span>
    </span>
    <span class="status" :class="{ danger: attention }">{{ card.status }}</span>
    <span v-for="p in card.plates" :key="p.plate_id" class="chip plate">{{ p.plate_id.replace(/^plate-/, 'P-').slice(0, 10) }} · {{ p.printer_id }}</span>
  </article>
</template>

<style scoped>
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
