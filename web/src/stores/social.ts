// Social Listening: leads, recent captures, the screen's numbers, the social settings and capture tokens.
// Every change is a command (social.*, config.set); the social agent answers on the live feed (evt.social.*),
// which reloads this store. Nothing here, or anywhere, posts a reply: the person pastes and posts it.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, command, errorText } from '@/lib/api'
import { type Capture, type CaptureToken, type Label, type Lead, type SocialStats, sortLeads } from '@/lib/social'
import type { Setting } from '@/lib/types'

export const SOCIAL_SETTINGS = ['social.enabled', 'social.intent_threshold', 'social.prefilter_min', 'social.daily_budget_usd'] as const

export const useSocial = defineStore('social', () => {
  const leads = ref<Lead[]>([])
  const captures = ref<Capture[]>([])
  const stats = ref<SocialStats | null>(null)
  const settings = ref<Setting[]>([])
  const tokens = ref<CaptureToken[]>([])
  const leadFilter = ref<'open' | 'posted' | 'dismissed' | 'all'>('open')
  const captureFilter = ref<string | null>(null)
  const error = ref('')
  const loaded = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  const setting = (key: string) => settings.value.find((s) => s.key === key)
  const threshold = computed(() => Number(setting('social.intent_threshold')?.value ?? 0.7))
  const budget = computed(() => Number(setting('social.daily_budget_usd')?.value ?? 2))
  const pending = computed(() => leads.value.filter((l) => l.status === 'pending'))

  async function load() {
    try {
      const q = captureFilter.value ? `&status=${captureFilter.value}` : ''
      const [l, c, s, cfg] = await Promise.all([
        api<{ leads: Lead[] }>(`/api/v1/social/leads?status=${leadFilter.value}`),
        api<{ captures: Capture[] }>(`/api/v1/social/captures?limit=100${q}`),
        api<SocialStats>('/api/v1/social/stats'),
        api<{ settings: Setting[] }>('/api/v1/system/config'),
      ])
      leads.value = sortLeads(l.leads)
      captures.value = c.captures
      stats.value = s
      settings.value = cfg.settings.filter((x) => (SOCIAL_SETTINGS as readonly string[]).includes(x.key))
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }

  /** Coalesce bursts of live events into one reload. */
  function reloadSoon(ms = 600) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      void load()
    }, ms)
  }

  async function loadTokens() {
    tokens.value = (await api<{ tokens: CaptureToken[] }>('/api/v1/social/tokens')).tokens
  }

  async function createToken(label: string): Promise<string> {
    const r = await api<{ token: string }>('/api/v1/social/tokens', { method: 'POST', body: { label } })
    await loadTokens()
    return r.token
  }

  async function revokeToken(id: string) {
    await api(`/api/v1/social/tokens/${encodeURIComponent(id)}/revoke`, { method: 'POST' })
    await loadTokens()
  }

  const addPost = (post_text: string, group_name: string, post_url: string) =>
    command('social.capture', { post_text, group_name, post_url })

  /** Optimistic: the lead shows its new state at once; the live feed confirms (or the reload corrects it). */
  async function decide(lead: Lead, approve: boolean, finalText = '') {
    const before = lead.status
    lead.status = approve ? 'approved' : 'dismissed'
    if (approve) lead.final_text = finalText
    try {
      await command('social.decide', { lead_id: lead.lead_id, approve, final_text: finalText })
    } catch (err) {
      lead.status = before
      throw err
    }
  }

  async function markPosted(lead: Lead) {
    const before = lead.status
    lead.status = 'posted'
    try {
      await command('social.posted', { lead_id: lead.lead_id })
    } catch (err) {
      lead.status = before
      throw err
    }
  }

  async function label(c: Capture, value: Label) {
    const before = c.label
    c.label = value
    try {
      await command('social.label', { capture_id: c.capture_id, label: value })
    } catch (err) {
      c.label = before
      throw err
    }
  }

  async function rescore(c: Capture) {
    await command('social.rescore', { capture_id: c.capture_id })
    c.status = 'new'
    c.status_reason = ''
  }

  async function setSetting(key: string, value: unknown) {
    await command('config.set', { key, value })
    const s = setting(key)
    if (s) s.value = value
  }

  return {
    leads, captures, stats, settings, tokens, leadFilter, captureFilter, error, loaded, threshold, budget, pending,
    setting, load, reloadSoon, loadTokens, createToken, revokeToken, addPost, decide, markPosted, label, rescore, setSetting,
  }
})
