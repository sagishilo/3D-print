<script setup lang="ts">
// The Kanban board: one column per stage. Dropping a card asks the coordinator to move it; columns that cannot take
// the dragged card say why. Cards that need attention stay in the column they came from. Keyboard / touch: open a
// card (Enter) and use the actions in the order panel.
import { computed, ref } from 'vue'

import type { OrderCard } from '@/lib/order-types'
import { COLUMNS, type DropAction, dropAction, type Stage } from '@/lib/orders'

import OrderCardView from './OrderCard.vue'

const props = defineProps<{ cards: OrderCard[] }>()
const emit = defineEmits<{ open: [id: string]; drop: [card: OrderCard, action: DropAction] }>()

const dragging = ref<OrderCard | null>(null)
const over = ref<Stage | null>(null)

const columns = computed(() => COLUMNS.map((c) => ({ ...c, cards: props.cards.filter((o) => o.stage === c.stage && o.state !== 'CANCELLED') })))
const verdict = (stage: Stage) => (dragging.value ? dropAction(dragging.value, stage) : null)

function onDragStart(e: DragEvent, card: OrderCard) {
  dragging.value = card
  e.dataTransfer?.setData('text/plain', card.order_id)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
function onDragEnd() {
  dragging.value = null
  over.value = null
}
function onDrop(stage: Stage) {
  const card = dragging.value
  onDragEnd()
  if (card && card.stage !== stage) emit('drop', card, dropAction(card, stage))
}
</script>

<template>
  <div class="board" role="list" aria-label="Order pipeline">
    <section
      v-for="col in columns"
      :key="col.stage"
      class="column"
      role="listitem"
      :class="{ over: over === col.stage, can: verdict(col.stage)?.ok, cannot: verdict(col.stage) && !verdict(col.stage)?.ok }"
      :data-testid="`column-${col.stage}`"
      @dragover.prevent="over = col.stage"
      @dragleave="over === col.stage && (over = null)"
      @drop.prevent="onDrop(col.stage)"
    >
      <header>
        <h3>{{ col.label }}</h3>
        <span class="n">{{ col.cards.length }}</span>
      </header>
      <p class="hint">
        {{ dragging ? (verdict(col.stage)?.ok ? 'Drop here' : (verdict(col.stage) as { reason: string }).reason) : col.hint }}
      </p>
      <OrderCardView
        v-for="c in col.cards"
        :key="c.order_id"
        :card="c"
        @open="emit('open', $event)"
        @dragstart="onDragStart($event, c)"
        @dragend="onDragEnd"
      />
    </section>
  </div>
</template>

<style scoped>
.board {
  display: grid;
  grid-template-columns: repeat(7, minmax(150px, 1fr));
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 6px;
}
.column {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 220px;
  padding: 10px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface);
  transition: border-color 0.1s ease, background 0.1s ease;
}
.column.can {
  border-style: dashed;
  border-color: var(--accent);
}
.column.cannot {
  opacity: 0.6;
}
.column.over.can {
  background: var(--accent-bg);
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
h3 {
  font: 600 13px var(--font);
}
.n {
  font: 600 12px var(--font-mono);
  color: var(--muted);
}
.hint {
  font-size: 11px;
  color: var(--idle);
  min-height: 14px;
}
</style>
