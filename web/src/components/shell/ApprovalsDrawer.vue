<script setup lang="ts">
import { ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import { useApprovals } from '@/stores/approvals'
import { useToasts } from '@/stores/toasts'
import { vCan } from '@/directives/can'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const approvals = useApprovals()
const toasts = useToasts()
const busy = ref<string | null>(null)

async function decide(id: string, approved: boolean) {
  busy.value = id
  try {
    await approvals.decide(id, approved)
    toasts.ok(approved ? 'Approved' : 'Rejected', 'The coordinator records the decision.')
  } catch (err) {
    toasts.error('Decision failed', errorText(err))
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div v-if="open" class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <aside class="drawer" role="dialog" aria-label="Approvals inbox" aria-modal="true">
      <div class="head">
        <h2>Approvals · {{ approvals.count }} pending</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </div>
      <p class="muted">Purchase orders above the auto-approve limit (and later, social replies and cancellations) wait here.</p>
      <div v-if="!approvals.pending.length" class="empty">Nothing waits for you.</div>
      <article v-for="a in approvals.pending" :key="a.approval_id" class="approval">
        <div class="row">
          <span class="chip">{{ a.action }}</span>
          <span class="muted small">{{ dateTime(a.created_at) }} · {{ a.requested_by }}</span>
        </div>
        <p class="summary">{{ a.summary }}</p>
        <div class="row actions">
          <button v-can="'operator'" type="button" class="btn btn-sm" :disabled="busy === a.approval_id" @click="decide(a.approval_id, false)">Reject</button>
          <button v-can="'operator'" type="button" class="btn btn-sm btn-primary" :disabled="busy === a.approval_id" @click="decide(a.approval_id, true)">
            <Icon name="check" :size="14" />Approve
          </button>
        </div>
      </article>
    </aside>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  z-index: 40;
  display: flex;
  justify-content: flex-end;
}
.drawer {
  width: min(420px, 100vw);
  height: 100%;
  overflow-y: auto;
  background: var(--surface);
  border-left: 1px solid var(--border);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head h2 {
  font: 600 16px var(--font);
}
.approval {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.actions {
  justify-content: flex-end;
}
.summary {
  font-size: 13px;
}
.small {
  font-size: 12px;
}
</style>
