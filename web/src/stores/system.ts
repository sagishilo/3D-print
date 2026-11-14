import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, command, errorText } from '@/lib/api'
import type { AgentRow, DeadLetter, NatsConnection, Overview, Setting, StreamRow } from '@/lib/types'

export const useSystem = defineStore('system', () => {
  const overview = ref<Overview | null>(null)
  const agents = ref<AgentRow[]>([])
  const connections = ref<NatsConnection[]>([])
  const streams = ref<StreamRow[]>([])
  const dlq = ref<DeadLetter[]>([])
  const dlqTotal = ref(0)
  const settings = ref<Setting[]>([])
  const error = ref('')
  const loadedAt = ref(0)

  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try {
      const out = await fn()
      error.value = ''
      loadedAt.value = Date.now()
      return out
    } catch (err) {
      error.value = errorText(err)
      return undefined
    }
  }

  const loadAgents = () =>
    guarded(async () => {
      agents.value = (await api<{ agents: AgentRow[] }>('/api/v1/system/agents')).agents
    })
  const loadOverview = () =>
    guarded(async () => {
      overview.value = await api<Overview>('/api/v1/system/overview')
    })
  const loadConnections = () =>
    guarded(async () => {
      connections.value = (await api<{ connections: NatsConnection[] }>('/api/v1/system/connections')).connections
    })
  const loadStreams = () =>
    guarded(async () => {
      streams.value = (await api<{ streams: StreamRow[] }>('/api/v1/system/streams')).streams
    })
  const loadDlq = () =>
    guarded(async () => {
      const r = await api<{ items: DeadLetter[]; total: number }>('/api/v1/system/dlq?limit=100')
      dlq.value = r.items
      dlqTotal.value = r.total
    })
  const loadSettings = () =>
    guarded(async () => {
      settings.value = (await api<{ settings: Setting[] }>('/api/v1/system/config')).settings
    })

  /** Audited: the coordinator records who changed what (coordinator.config_changes) and agents pick it up in ~5 s. */
  async function setSetting(key: string, value: unknown) {
    const s = settings.value.find((x) => x.key === key)
    const previous = s?.value
    if (s) s.value = value
    try {
      await command('config.set', { key, value })
    } catch (err) {
      if (s) s.value = previous
      throw err
    }
  }

  async function replayDlq(seq: number) {
    await api(`/api/v1/system/dlq/${seq}/replay`, { method: 'POST' })
    dlq.value = dlq.value.filter((d) => d.seq !== seq)
    dlqTotal.value = Math.max(dlqTotal.value - 1, 0)
  }

  async function discardDlq(seq: number) {
    await api(`/api/v1/system/dlq/${seq}/discard`, { method: 'POST' })
    dlq.value = dlq.value.filter((d) => d.seq !== seq)
    dlqTotal.value = Math.max(dlqTotal.value - 1, 0)
  }

  const coreAgents = computed(() => agents.value.filter((a) => a.tier === 'core'))
  const unhealthy = computed(() => coreAgents.value.filter((a) => a.status !== 'healthy').length)

  return {
    overview, agents, connections, streams, dlq, dlqTotal, settings, error, loadedAt, coreAgents, unhealthy,
    loadAgents, loadOverview, loadConnections, loadStreams, loadDlq, loadSettings, setSetting, replayDlq, discardDlq,
  }
})
