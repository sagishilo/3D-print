// Integrations (Wave 2 / W2-0): the connectors (outside services), their syncs and the notifications outbox.
// Changes are owner commands; the integrations agent answers on the live feed, which reloads this store.
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, command, errorText } from '@/lib/api'

export type Health = 'off' | 'waiting' | 'ok' | 'failing' | 'down'

export interface Connector {
  connector_id: string
  kind: string
  label: string
  enabled: boolean
  mode: 'fake' | 'live'
  interval_s: number
  secrets_status: Record<string, boolean>
  secrets_missing: string[]
  health: Health
  last_run_at: string | null
  last_ok_at: string | null
  last_error: string | null
  consecutive_failures: number
  next_run_at: string | null
  runs: number
  items: number
  mapped: number
  updated_by: string
}

export interface SyncRun {
  id: number
  trigger: string
  started_at: string
  finished_at: string | null
  ok: boolean | null
  items: number
  created: number
  updated: number
  error: string | null
}

export interface Notification {
  id: number
  topic: string
  severity: 'info' | 'warning' | 'critical'
  title: string
  body: string
  link: string
  status: 'pending' | 'sent' | 'held' | 'failed' | 'skipped' | 'dashboard'
  created_at: string
}

export interface ConnectorHealth {
  connector_id: string
  label: string
  kind: string
  enabled: boolean
  mode: string
  health: Health
  last_ok_at: string | null
  last_error: string | null
}

export const useIntegrations = defineStore('integrations', () => {
  const connectors = ref<Connector[]>([])
  const runs = ref<Record<string, SyncRun[]>>({})
  const notifications = ref<Notification[]>([])
  const error = ref('')
  const loaded = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  async function load() {
    try {
      const [c, n] = await Promise.all([
        api<{ connectors: Connector[] }>('/api/v1/integrations'),
        api<{ notifications: Notification[] }>('/api/v1/notifications'),
      ])
      connectors.value = c.connectors
      notifications.value = n.notifications
      const r = await Promise.all(c.connectors.map((x) =>
        api<{ runs: SyncRun[] }>(`/api/v1/integrations/${encodeURIComponent(x.connector_id)}/runs?limit=5`).then((v) => [x.connector_id, v.runs] as const)))
      runs.value = Object.fromEntries(r)
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }

  function reloadSoon(ms = 400) {
    if (!loaded.value) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), ms)
  }

  return {
    connectors, runs, notifications, error, loaded, load, reloadSoon,
    configure: (id: string, change: { enabled?: boolean; mode?: 'fake' | 'live'; interval_s?: number }) =>
      command('integrations.configure', { connector_id: id, ...change }),
    runNow: (id: string) => command('integrations.run', { connector_id: id }),
  }
})
