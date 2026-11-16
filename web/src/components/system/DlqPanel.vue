<script setup lang="ts">
// Dead letters (DLQ stream, 90 days): messages an agent gave up on after its retries. Replay publishes the original
// message again (agents that already handled it skip it by its id); Discard deletes it. Both are audited.
import { computed, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import type { DeadLetter } from '@/lib/types'
import { useConfirm } from '@/stores/confirm'
import { useSystem } from '@/stores/system'
import { useToasts } from '@/stores/toasts'

const props = withDefaults(defineProps<{ limit?: number }>(), { limit: 100 })

const system = useSystem()
const confirm = useConfirm()
const toasts = useToasts()
const selectedSeq = ref<number | null>(null)
const busy = ref(false)

const items = computed(() => system.dlq.slice(0, props.limit))
const selected = computed(() => system.dlq.find((d) => d.seq === selectedSeq.value) ?? system.dlq[0] ?? null)
const inspected = computed(() => (selected.value ? JSON.stringify(selected.value.original ?? selected.value, null, 2) : ''))

function shortId(id: string | null): string {
  return id && id.length > 10 ? `${id.slice(0, 4)}…${id.slice(-3)}` : (id ?? '—')
}

async function replay(d: DeadLetter) {
  const ok = await confirm.ask({
    title: 'Replay this message?',
    text: `${d.original_subject ?? 'The message'} is published again. Agents that already handled it skip it; ${d.consumer ?? 'the consumer'} that failed gets another try.`,
    confirmLabel: 'Replay',
  })
  if (!ok) return
  busy.value = true
  try {
    await system.replayDlq(d.seq)
    selectedSeq.value = null
    toasts.ok('Message replayed', d.original_subject ?? undefined)
  } catch (err) {
    toasts.error('Replay failed', errorText(err))
  } finally {
    busy.value = false
  }
}

async function discard(d: DeadLetter) {
  const ok = await confirm.ask({
    title: 'Discard this message?',
    text: `${d.original_subject ?? 'The message'} is deleted from the dead letter queue for good and never processed. The discard is recorded in the audit log.`,
    confirmLabel: 'Discard',
    danger: true,
  })
  if (!ok) return
  busy.value = true
  try {
    await system.discardDlq(d.seq)
    selectedSeq.value = null
    toasts.info('Message discarded', d.original_subject ?? undefined)
  } catch (err) {
    toasts.error('Discard failed', errorText(err))
  } finally {
    busy.value = false
  }
}

async function replayAll() {
  const list = [...system.dlq]
  const ok = await confirm.ask({
    title: `Replay all ${list.length} messages?`,
    text: 'Each message is published again, oldest first. Messages that fail again come back to this queue.',
    confirmLabel: 'Replay all',
  })
  if (!ok) return
  busy.value = true
  let done = 0
  let failed = 0
  for (const d of list.reverse()) {
    try {
      await system.replayDlq(d.seq)
      done += 1
    } catch {
      failed += 1
    }
  }
  busy.value = false
  selectedSeq.value = null
  if (failed) toasts.error(`Replayed ${done}, ${failed} failed`, 'See the remaining messages in the queue.')
  else toasts.ok(`Replayed ${done} message${done === 1 ? '' : 's'}`)
  void system.loadDlq()
}
</script>

<template>
  <div class="dlq" :class="{ split: selected }">
    <section class="panel" data-tour="dlq">
      <div class="panel-head">
        <h2>Dead letter queue</h2>
        <button type="button" class="btn btn-sm" :disabled="busy || !system.dlq.length" @click="replayAll">
          <Icon name="refresh" :size="14" />Replay all
        </button>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th scope="col">Subject</th>
              <th scope="col">Last error</th>
              <th scope="col" class="num">Attempts</th>
              <th scope="col">Dead-lettered</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="d in items"
              :key="d.seq"
              class="selectable"
              :class="{ selected: selected?.seq === d.seq }"
              :aria-selected="selected?.seq === d.seq"
              tabindex="0"
              :data-testid="`dlq-${d.seq}`"
              @click="selectedSeq = d.seq"
              @keydown.enter="selectedSeq = d.seq"
            >
              <td class="mono">{{ d.original_subject ?? '—' }}</td>
              <td class="err" :title="d.error ?? ''">{{ d.error ?? '—' }}</td>
              <td class="num">{{ d.deliveries ?? '—' }}</td>
              <td class="num nowrap">{{ dateTime(d.time) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!system.dlq.length" class="empty">The dead letter queue is empty: every message was handled.</p>
        <p v-else-if="system.dlqTotal > items.length" class="panel-note">Showing the newest {{ items.length }} of {{ system.dlqTotal }}.</p>
      </div>
    </section>

    <section v-if="selected" class="panel" data-testid="dlq-inspector">
      <div class="panel-head">
        <h2>Message inspector</h2>
        <span class="chip" :title="selected.original_id ?? ''">msg {{ shortId(selected.original_id) }}</span>
      </div>
      <p class="error-line"><Icon name="alert" :size="14" /> {{ selected.error ?? 'No error recorded' }}</p>
      <p class="panel-note">
        Failed in <span class="mono">{{ selected.consumer ?? 'an unknown consumer' }}</span> after
        {{ selected.deliveries ?? '?' }} attempts · {{ dateTime(selected.time) }}
      </p>
      <pre class="code">{{ inspected }}</pre>
      <div class="actions">
        <button type="button" class="btn btn-primary" :disabled="busy" data-testid="dlq-replay" @click="replay(selected)">
          <Icon name="refresh" :size="14" />Replay
        </button>
        <button type="button" class="btn btn-danger" :disabled="busy" data-testid="dlq-discard" @click="discard(selected)">
          <Icon name="x" :size="14" />Discard
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dlq {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
@media (min-width: 1100px) {
  .dlq.split {
    grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  }
}
.nowrap {
  white-space: nowrap;
}
.err {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #ffb4ae;
}
.error-line {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  color: var(--danger);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
