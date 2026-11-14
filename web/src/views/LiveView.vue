<script setup lang="ts">
// Live floor: every printer's camera and state, safety alerts, a printer's telemetry replay and the event feed.
import { computed, nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import EventFeed from '@/components/live/EventFeed.vue'
import PrinterTile from '@/components/live/PrinterTile.vue'
import TelemetryReplay from '@/components/live/TelemetryReplay.vue'
import Icon from '@/components/ui/Icon.vue'
import { clock } from '@/lib/format'
import { useLive } from '@/stores/live'
import { type DisplayStatus, STATUS_TONE, usePrinters } from '@/stores/printers'

const route = useRoute()
const router = useRouter()
const printers = usePrinters()
const live = useLive()
const replayEl = ref<HTMLElement | null>(null)

const selected = computed(() => {
  const id = typeof route.query.printer === 'string' ? route.query.printer : null
  return id && printers.get(id) ? id : null
})

async function select(id: string | null) {
  await router.replace({ query: id ? { ...route.query, printer: id } : {} })
  if (selected.value) {
    await nextTick()
    replayEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}
const toggle = (id: string) => select(id === selected.value ? null : id)

const ORDER: DisplayStatus[] = ['printing', 'heating', 'paused', 'failed', 'finished', 'idle', 'offline']
const counts = computed(() => ORDER.filter((s) => printers.counts[s]).map((s) => ({ s, n: printers.counts[s], tone: STATUS_TONE[s] })))

const gapText = computed(() => {
  const g = live.gap
  if (!g) return ''
  if (g.reset) return `The ${g.stream} stream restarted since this tab last saw it, so the feed starts fresh.`
  return `This tab was away for a while: ${g.gap ?? 'some'} older ${g.stream} events were skipped and only the newest were replayed. Printer state below is a fresh snapshot.`
})
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Live floor</h1>
      <p>
        {{ printers.list.length }} printer{{ printers.list.length === 1 ? '' : 's' }} · telemetry via MQTT / Moonraker · cameras via
        go2rtc → MJPEG · history replays from JetStream
      </p>
    </div>
    <ul class="counts" aria-label="Printer states">
      <li v-for="c in counts" :key="c.s" class="pill" :class="`tone-${c.tone}`"><span class="dot" aria-hidden="true" />{{ c.n }} {{ c.s }}</li>
    </ul>
  </div>

  <div v-for="a in live.alerts.slice(0, 3)" :key="a.key" class="banner danger" role="alert">
    <Icon name="alert" :size="18" />
    <div class="banner-text">
      <b>{{ a.d.text }}</b>
      <span class="muted">{{ clock(a.at) }} · {{ a.item.topic }}</span>
    </div>
    <div class="banner-actions">
      <button v-if="a.d.printerId" type="button" class="btn btn-sm" @click="select(a.d.printerId)">
        <Icon name="history" :size="14" />Replay
      </button>
      <button type="button" class="btn btn-sm" @click="live.acknowledge(a.key)"><Icon name="check" :size="14" />Acknowledge</button>
    </div>
  </div>

  <div v-if="gapText" class="banner warn" role="status">
    <Icon name="history" :size="18" />
    <span class="banner-text">{{ gapText }}</span>
    <button type="button" class="btn btn-sm" @click="live.gap = null">Dismiss</button>
  </div>

  <p v-if="printers.error" class="banner danger" role="alert">
    <Icon name="alert" :size="18" /><span class="banner-text">Could not load the printers: {{ printers.error }}</span>
  </p>

  <section class="tiles" aria-label="Printers">
    <PrinterTile v-for="p in printers.list" :key="p.id" :printer="p" :selected="p.id === selected" @history="toggle" />
  </section>
  <p v-if="!printers.list.length && !printers.loading && !printers.error" class="panel empty">
    No printers yet. They come from config/fleet.toml (or fleet.sim.toml for the simulator) once the fleet agent runs.
  </p>

  <div ref="replayEl" class="grid-2 lower">
    <TelemetryReplay v-if="selected" :printer-id="selected" @close="select(null)" />
    <EventFeed :printer-id="selected" />
  </div>
</template>

<style scoped>
.counts {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  list-style: none;
  margin: 0;
  padding: 0;
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: 16px;
}
.banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--radius);
  border: 1px solid;
  flex-wrap: wrap;
}
.banner.danger {
  background: var(--danger-bg);
  border-color: var(--danger);
  color: var(--danger);
}
.banner.warn {
  background: var(--warn-bg);
  border-color: var(--warn);
  color: var(--warn);
}
.banner-text {
  flex: 1 1 260px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--text);
  font-size: 13px;
}
.banner-actions {
  display: flex;
  gap: 8px;
}
.lower {
  scroll-margin-top: 80px;
}
</style>
