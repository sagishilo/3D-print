import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, errorText } from '@/lib/api'
import type { Printer, PrinterState, PrinterStatus, TelemetrySample, Tone } from '@/lib/types'

export type DisplayStatus = PrinterStatus | 'heating'

/** What a tile shows: offline beats everything; "heating" = printing but still far from the targets at 0 %. */
export function displayStatus(p: Printer): DisplayStatus {
  if (!p.online || !p.state) return 'offline'
  const s = p.state
  if (s.status === 'printing' && s.progress === 0 && s.nozzle_target && (s.nozzle_temp ?? 0) < s.nozzle_target - 5) {
    return 'heating'
  }
  return s.status
}

export const STATUS_TONE: Record<DisplayStatus, Tone> = {
  printing: 'ok',
  heating: 'warn',
  paused: 'warn',
  finished: 'accent',
  failed: 'danger',
  idle: 'idle',
  offline: 'idle',
}

export const usePrinters = defineStore('printers', () => {
  const list = ref<Printer[]>([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    try {
      list.value = (await api<{ printers: Printer[] }>('/api/v1/printers')).printers
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loading.value = false
    }
  }

  function get(id: string): Printer | undefined {
    return list.value.find((p) => p.id === id)
  }

  /** A telemetry sample from the live feed (tlm.<id>.state). */
  function applyState(state: PrinterState) {
    const p = get(state.printer_id)
    if (!p) return
    p.state = state
    p.online = true
  }

  function setOnline(id: string, online: boolean) {
    const p = get(id)
    if (p) p.online = online
  }

  const counts = computed(() => {
    const c: Record<DisplayStatus, number> = { printing: 0, heating: 0, paused: 0, finished: 0, failed: 0, idle: 0, offline: 0 }
    for (const p of list.value) c[displayStatus(p)] += 1
    return c
  })

  async function history(id: string, minutes: number) {
    return api<{ samples: TelemetrySample[]; count: number }>(
      `/api/v1/printers/${encodeURIComponent(id)}/telemetry?minutes=${minutes}&points=300`,
    )
  }

  return { list, loading, error, load, get, applyState, setOnline, counts, history }
})
