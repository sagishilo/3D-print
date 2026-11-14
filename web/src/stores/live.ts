// The live feed for the whole app: one WebSocket, resumed after every reconnect (see lib/live.ts).
import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'

import { refreshSession } from '@/lib/api'
import { type Described, describe } from '@/lib/events'
import { ago } from '@/lib/format'
import { type GapInfo, LiveClient, type LiveStatus, type ReplayEnd } from '@/lib/live'
import type { FeedItem, PrinterState, Tone } from '@/lib/types'

import { useApprovals } from './approvals'
import { useAuth } from './auth'
import { useOrders } from './orders'
import { usePrinters } from './printers'
import { useSystem } from './system'
import { useToasts } from './toasts'

export interface FeedEntry {
  key: string // stream:seq
  item: FeedItem
  d: Described
  at: string
  replayed: boolean
}

const FEED_MAX = 600
export const TOPICS = ['evt.>', 'tlm.*.state', 'dlq.>']

export const useLive = defineStore('live', () => {
  const status = ref<LiveStatus>('idle')
  const lastMessageAt = ref(0)
  const now = ref(Date.now())
  const feed = shallowRef<FeedEntry[]>([])
  const replay = ref<{ id: string; since: Date; done: boolean; count: number; truncated: boolean } | null>(null)
  const gap = ref<GapInfo | null>(null)
  let client: LiveClient | null = null
  let ticker: ReturnType<typeof setInterval> | null = null

  const printers = usePrinters()
  const toasts = useToasts()

  // Entries are merged in batches (one sort and one render per 50 ms), so a replay of 2000 events stays smooth.
  let pending: FeedEntry[] = []
  let flushTimer: ReturnType<typeof setTimeout> | null = null

  function insert(entry: FeedEntry) {
    pending.push(entry)
    flushTimer ??= setTimeout(flush, 50)
  }

  function flush() {
    flushTimer = null
    const keys = new Set(feed.value.map((e) => e.key))
    const fresh = pending.filter((e) => !keys.has(e.key) && Boolean(keys.add(e.key))) // seen live and in a replay
    pending = []
    if (!fresh.length) return
    const next = [...feed.value, ...fresh].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
    feed.value = next.length > FEED_MAX ? next.slice(0, FEED_MAX) : next
  }

  function sideEffects(entry: FeedEntry) {
    const { d } = entry
    const data = entry.item.data.data as Record<string, any>
    if (d.kind === 'printer.connection.changed') printers.setOnline(data.printer_id, data.online)
    if (d.kind.startsWith('fleet.plate.') || d.kind.startsWith('printer.print.') || d.kind.startsWith('printer.calibration.'))
      void printers.load()
    if (d.kind === 'printer.calibration.finished') {
      if (data.succeeded) toasts.ok('Calibration done', d.text)
      else toasts.error(data.started === false ? 'Calibration not started' : 'Calibration failed', d.text)
    }
    if (d.safety) toasts.push({ tone: 'danger', title: 'Safety: pause not confirmed', text: d.text, sticky: true })
    if (d.kind === 'printer.pause.confirmed') toasts.ok('Printer paused', d.text)
    if (d.kind.startsWith('approval.')) void useApprovals().load()
    if (d.kind.startsWith('order.') || d.kind.startsWith('fleet.') || d.kind.startsWith('personalization.')) useOrders().reloadSoon()
    if (d.kind === 'order.move.rejected') toasts.error(`Order ${data.order_id} not moved`, String(data.reason))
    if (d.kind === 'message.dead_lettered') void useSystem().loadDlq()
    if (d.kind === 'system.config.changed') void useSystem().loadSettings()
  }

  function onItem(item: FeedItem) {
    lastMessageAt.value = Date.now()
    if (item.topic.startsWith('tlm.')) {
      printers.applyState(item.data.data as unknown as PrinterState)
      return
    }
    const entry: FeedEntry = {
      key: `${item.stream}:${item.seq}`,
      item,
      d: describe(item.data),
      at: item.data.time ?? item.ts ?? new Date().toISOString(),
      replayed: item.type === 'replay',
    }
    insert(entry)
    if (item.type === 'event') sideEffects(entry)
  }

  function onReplayEnd(id: string, info: ReplayEnd) {
    if (replay.value?.id === id) replay.value = { ...replay.value, done: true, count: info.count, truncated: info.truncated }
  }

  function start() {
    if (client) return
    client = new LiveClient({
      topics: TOPICS,
      refresh: refreshSession,
      onItem,
      onStatus: (s) => {
        status.value = s
        if (s === 'live') lastMessageAt.value = Date.now() // "subscribed" just arrived
      },
      onGap: (g) => {
        gap.value = g
        void printers.load() // too far behind to replay everything: take a fresh snapshot instead
      },
      onReplayEnd,
      onUnauthorized: () => useAuth().expired(),
    })
    client.start()
    // The client sees every frame, heartbeats included (every 15 s on a quiet feed): that is "last message".
    ticker ??= setInterval(() => {
      now.value = Date.now()
      if (client) lastMessageAt.value = Math.max(lastMessageAt.value, client.lastMessageAt)
    }, 500)
  }

  function stop() {
    client?.stop()
    client = null
    if (ticker) clearInterval(ticker)
    ticker = null
    if (flushTimer) clearTimeout(flushTimer)
    flushTimer = null
    pending = []
    feed.value = []
  }

  /** Replay stored events from the last ``minutes`` into the feed (EVENTS keeps 30 days). */
  function replaySince(minutes: number, topics = ['evt.>']) {
    if (!client) return
    const since = new Date(Date.now() - minutes * 60_000)
    replay.value = { id: client.replay(topics, since, 2000), since, done: false, count: 0, truncated: false }
  }

  // Safety alerts for the banner on the Live floor: unverified pauses and critical failures seen live in this tab,
  // newest first, until the operator acknowledges them.
  const acknowledged = ref<string[]>([])
  const alerts = computed(() =>
    feed.value.filter(
      (e) =>
        !e.replayed &&
        !acknowledged.value.includes(e.key) &&
        (e.d.safety || (e.d.kind === 'printer.failure.detected' && e.d.tone === 'danger')),
    ),
  )
  function acknowledge(key: string) {
    acknowledged.value = [...acknowledged.value, key]
  }

  const tone = computed<Tone>(() => {
    if (status.value === 'live') return now.value - lastMessageAt.value < 30_000 ? 'ok' : 'warn'
    if (status.value === 'connecting' || status.value === 'reconnecting') return 'warn'
    return 'danger'
  })

  const label = computed(() => {
    switch (status.value) {
      case 'live':
        return `Live · ${ago(lastMessageAt.value, now.value)} ago`
      case 'connecting':
        return 'Connecting…'
      case 'reconnecting':
        return 'Reconnecting…'
      case 'blocked':
        return 'Feed blocked'
      default:
        return 'Offline'
    }
  })

  /** For narrow screens: "Live · 0.4 s". */
  const shortLabel = computed(() => label.value.replace(/ ago$/, ''))

  return { status, lastMessageAt, now, feed, replay, gap, alerts, tone, label, shortLabel, start, stop, replaySince, acknowledge }
})
