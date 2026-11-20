// Schedule (Wave 2 / W2-C): the timeline forecast per printer and the queue (gateway/app/schedule.py), plus each
// printer's maintenance and plate swap. Changes are commands to the fleet agent; its events reload this store.
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, command, errorText } from '@/lib/api'

export type JobAction = 'hold' | 'release' | 'pin' | 'unpin' | 'top' | 'up' | 'down' | 'auto'
export type WhyNot = '' | 'offline' | 'calibrating' | 'maintenance' | 'bed' | 'stack'

export interface Block {
  start: string
  end: string
  running: boolean
  note: string // running blocks: plate status (scheduled | printing); planned: "after quiet hours" or ""
  jobs: { job_id: string; order_id: string | null; reason: string }[]
}

export interface Lane {
  printer_id: string
  material: string
  color: string
  online: boolean
  available: boolean
  why_not: WhyNot
  auto_eject: boolean
  plate_stack: number
  plates_left: number
  blocks: Block[]
}

export interface QueuedJob {
  job_id: string
  order_id: string
  product_type: string | null
  material: string
  color: string
  est_minutes: number
  priority: number
  due_by: string | null
  hold: boolean
  pinned_printer: string | null
  rank: number | null
  planned_printer: string | null
  planned_start: string | null
  reason: string
}

export interface Unplaced {
  material: string
  color: string
  jobs: string[]
  minutes: number
}

export interface ScheduleWindow {
  overnight_from: number
  quiet_enabled: boolean
  quiet_from: number
  quiet_until: number
  long_job_hours: number
  quiet_now: boolean
}

export interface Schedule {
  now: string
  horizon_h: number
  lanes: Lane[]
  queue: QueuedJob[]
  unplaced: Unplaced[]
  window: ScheduleWindow
}

export interface MaintenanceTask {
  printer_id: string
  task_id: string
  label: string
  every_hours: number | null
  every_plates: number | null
  every_days: number | null
  block_when_overdue: boolean
  last_done_at: string | null
  state: 'ok' | 'due' | 'overdue'
  used: number
  left: string
  blocks: boolean
}

export interface PrinterMaintenance {
  printer_id: string
  counters: { print_hours: number; plates: number; kg: number }
  policy: { auto_eject: boolean; plate_stack: number; plates_left: number }
  tasks: MaintenanceTask[]
  log: { task_id: string; label: string; done_by: string; note: string; at: string }[]
}

export interface TaskInput {
  printer_id: string
  task_id: string
  label: string
  every_hours?: number | null
  every_plates?: number | null
  every_days?: number | null
  block_when_overdue?: boolean
  archived?: boolean
}

/** A task id from its label ("Clean the plate" -> "clean-the-plate"), as the contract's pattern allows. */
export function taskId(label: string): string {
  const slug = label.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
  return slug || `task-${Date.now().toString(36)}`
}

/** "18:00" from 18, "07:30" from 7.5. */
export function hourLabel(h: number): string {
  const m = Math.round((h % 1) * 60)
  return `${String(Math.floor(h)).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/** Where a block sits on a lane of `horizonH` hours from `now`, in percent (clipped to the lane). */
export function placeBlock(b: { start: string; end: string }, now: string, horizonH: number): { left: number; width: number } | null {
  const t0 = new Date(now).getTime()
  const span = horizonH * 3600_000
  const s = Math.max(0, new Date(b.start).getTime() - t0)
  const e = Math.min(span, new Date(b.end).getTime() - t0)
  if (e <= 0 || s >= span || e <= s) return null
  return { left: (s / span) * 100, width: Math.max(0.6, ((e - s) / span) * 100) }
}

export const useSchedule = defineStore('schedule', () => {
  const data = ref<Schedule | null>(null)
  const error = ref('')
  const loaded = ref(false)
  const maintenance = ref<Record<string, PrinterMaintenance>>({})
  let timer: ReturnType<typeof setTimeout> | null = null

  async function load() {
    try {
      data.value = await api<Schedule>('/api/v1/schedule')
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }

  async function loadMaintenance(printerId: string) {
    maintenance.value = { ...maintenance.value,
      [printerId]: await api<PrinterMaintenance>(`/api/v1/printers/${encodeURIComponent(printerId)}/maintenance`) }
  }

  function reloadSoon(ms = 500) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      if (loaded.value) void load()
      for (const id of Object.keys(maintenance.value)) void loadMaintenance(id).catch(() => undefined)
    }, ms)
  }

  return {
    data, error, loaded, maintenance, load, loadMaintenance, reloadSoon,
    control: (jobId: string, action: JobAction, printerId?: string) =>
      command('jobs.control', { job_id: jobId, action, ...(printerId ? { printer_id: printerId } : {}) }),
    policy: (printerId: string, change: { auto_eject?: boolean; plate_stack?: number; refill?: boolean }) =>
      command('printers.policy', { printer_id: printerId, ...change }),
    done: (printerId: string, taskId: string, note = '') => command('maintenance.done', { printer_id: printerId, task_id: taskId, note }),
    saveTask: (t: TaskInput) => command('maintenance.task', { ...t }),
  }
})
