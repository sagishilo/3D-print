import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CaptureCard from '@/components/social/CaptureCard.vue'
import LeadReview from '@/components/social/LeadReview.vue'
import { describe as describeEvent } from '@/lib/events'
import {
  accuracyLine,
  type Capture,
  captureLine,
  canRescore,
  intentTone,
  type Lead,
  leadsCsv,
  modelLine,
  replyText,
  type SocialStats,
  sortLeads,
} from '@/lib/social'
import { useSocial } from '@/stores/social'

import { json, on, serve } from './helpers'

beforeEach(() => {
  setActivePinia(createPinia())
  document.cookie = 'pf_csrf=tok'
})
afterEach(() => vi.unstubAllGlobals())

const lead = (over: Partial<Lead> = {}): Lead => ({
  lead_id: 'lead-1', capture_id: 'cap-1', intent_score: 0.92, sku: 'NP-DOOR', sku_name: 'Door nameplate', sku_price_agorot: 4500,
  summary: 'Wants a door sign.', draft: 'היי! נשמח להדפיס לך שלט', draft_notes: '', final_text: null, status: 'pending',
  status_reason: '', created_at: '2026-11-15T10:00:00Z', decided_by: null, decided_at: null, posted_by: null, posted_at: null,
  group_name: 'הורים בחיפה', post_url: 'https://www.facebook.com/groups/1/posts/2', post_text: 'מחפשת שלט לדלת', captured_by: 'admin',
  captured_at: '2026-11-15T10:00:00Z', intent: 'buy', ...over,
})

const capture = (over: Partial<Capture> = {}): Capture => ({
  capture_id: 'cap-1', group_name: 'הורים בחיפה', post_url: '', post_text: 'מחפשת שלט', captured_by: 'extension:laptop',
  captured_at: '2026-11-15T10:00:00Z', received_at: '2026-11-15T10:00:00Z', status: 'scored', status_reason: 'buy 0.55 < threshold 0.70',
  prefilter: 0.6, intent: 'buy', intent_score: 0.55, sku_guess: null, summary: '', label: null, labelled_by: null, ...over,
})

const stats = (over: Partial<SocialStats> = {}): SocialStats => ({
  captures_7d: { lead: 2, ignored: 5 }, captures_today: 3, groups_30d: 4, last_capture: '2026-11-15T10:00:00Z', leads: { pending: 1 },
  posted_7d: 1, spend_month: [{ model: 'claude-haiku-5-5', calls: 10, usd: 0.02, usd_today: 0.004, problems: 0 }],
  spend_today_usd: 0.004, accuracy: { labelled: 0, true_leads: 0, false_leads: 0, missed: 0, correctly_ignored: 0, precision: null,
    recall: null, agreement: null },
  models: { classify: 'claude-haiku-5-5', draft: 'claude-opus-5-5' },
  agent: { online: true, llm: 'claude', embedder: 'minilm', at: null }, ...over,
})

describe('social helpers', () => {
  it('says captures, never monitored or scanned (ADR-0007)', () => {
    const line = captureLine(stats(), Date.parse('2026-11-15T10:05:00Z'))
    expect(line).toMatch(/^Captures from 4 groups \(30 days\) · last capture .+ ago$/)
    expect(line).not.toMatch(/monitor|scan/i)
    expect(captureLine(stats({ last_capture: null }))).toMatch(/No captures yet/)
  })

  it('explains what the agent can do right now', () => {
    expect(modelLine(stats())).toBe('Triage claude-haiku-5-5 · drafts claude-opus-5-5')
    expect(modelLine(stats({ agent: { online: true, llm: 'none', embedder: null, at: null } }))).toMatch(/ANTHROPIC_API_KEY/)
    expect(modelLine(stats({ agent: { online: false, llm: null, embedder: null, at: null } }))).toBe('Social agent offline')
  })

  it('accuracy, ordering, reply text, rescoring and tones', () => {
    expect(accuracyLine(null)).toMatch(/Label captures/)
    expect(accuracyLine({ labelled: 1, true_leads: 0, false_leads: 0, missed: 0, correctly_ignored: 1, precision: null, recall: null,
      agreement: 1 })).toBe('1 labelled · no leads among them yet · no buyers among them yet')
    expect(accuracyLine({ labelled: 20, true_leads: 8, false_leads: 1, missed: 2, correctly_ignored: 9, precision: 0.889, recall: 0.8,
      agreement: 0.85 })).toBe('20 labelled · 89% of leads were buyers · 80% of buyers became leads')
    const sorted = sortLeads([lead({ lead_id: 'a', status: 'posted' }), lead({ lead_id: 'b', status: 'approved' }),
      lead({ lead_id: 'c', status: 'pending', created_at: '2026-11-15T09:00:00Z' }), lead({ lead_id: 'd', status: 'pending' })])
    expect(sorted.map((l) => l.lead_id)).toEqual(['d', 'c', 'b', 'a'])
    expect(replyText(lead(), '  edited  ')).toBe('edited')
    expect(replyText(lead({ final_text: 'approved text' }))).toBe('approved text')
    expect(canRescore({ status: 'waiting' }) && !canRescore({ status: 'lead' })).toBe(true)
    expect([intentTone(0.9, 0.7), intentTone(0.55, 0.7), intentTone(0.2, 0.7), intentTone(null, 0.7)]).toEqual(['ok', 'warn', 'idle', 'idle'])
  })

  it('exports leads as CSV that Excel reads in Hebrew', () => {
    const csv = leadsCsv([lead({ post_text: 'שלט, "גדול"\nלדלת' })])
    expect(csv.startsWith('﻿lead_id,created_at,status')).toBe(true)
    expect(csv).toContain('"שלט, ""גדול""\nלדלת"')
  })

  it('describes the social events on the live feed', () => {
    const ev = (type: string, data: Record<string, unknown>) => describeEvent({ type: `io.printfarm.${type}.v1`, data } as never)
    expect(ev('social.capture.scored', { status: 'lead', group_name: 'G', intent_score: 0.9, summary: 'needs a sign' }))
      .toMatchObject({ category: 'social', tone: 'ok', text: 'Lead in G · intent 0.90 · needs a sign' })
    expect(ev('social.capture.scored', { status: 'waiting', group_name: '', reason: 'no ANTHROPIC_API_KEY' }).tone).toBe('warn')
    expect(ev('social.lead.changed', { lead_id: 'lead-1', status: 'posted', by: 'admin' }).text).toBe('Lead lead-1 posted (admin)')
  })
})

describe('LeadReview', () => {
  it('approve & copy copies the edited text and approves it; then it can be marked posted', async () => {
    const writeText = vi.fn(async () => undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    const calls = serve(on('POST', /\/api\/v1\/commands\/social\./, () => json(202, { accepted: true, command_id: 'x'.repeat(16) })))
    const social = useSocial()
    social.leads = [lead()]
    const w = mount(LeadReview, { props: { leads: social.leads, draftModel: 'claude-opus-5-5' } })
    expect(w.get('[data-testid=lead-post]').text()).toBe('מחפשת שלט לדלת')
    expect(w.text()).toContain('Matched SKU: NP-DOOR · Door nameplate')
    expect(w.get('[data-testid=mark-posted]').attributes('disabled')).toBeDefined()     // approve first
    await w.get('[data-testid=lead-draft]').setValue('היי! שלט לדלת ב-45 ₪, נשלח לך פרטים בפרטי')
    await w.get('[data-testid=approve-copy]').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('היי! שלט לדלת ב-45 ₪, נשלח לך פרטים בפרטי')
    expect(calls.find((c) => c.url.endsWith('social.decide'))!.body).toEqual({ lead_id: 'lead-1', approve: true,
      final_text: 'היי! שלט לדלת ב-45 ₪, נשלח לך פרטים בפרטי' })
    expect(social.leads[0]!.status).toBe('approved')
    await w.setProps({ leads: [...social.leads] })
    await w.get('[data-testid=mark-posted]').trigger('click')
    await flushPromises()
    expect(calls.find((c) => c.url.endsWith('social.posted'))!.body).toEqual({ lead_id: 'lead-1' })
    w.unmount()
  })

  it('R rejects from the keyboard, but not while typing in the draft', async () => {
    const calls = serve(on('POST', /social\.decide/, () => json(202, { accepted: true, command_id: 'y'.repeat(16) })))
    const social = useSocial()
    social.leads = [lead()]
    const w = mount(LeadReview, { props: { leads: social.leads, draftModel: 'm' }, attachTo: document.body })
    w.get('[data-testid=lead-draft]').element.dispatchEvent(new KeyboardEvent('keydown', { key: 'r', bubbles: true }))
    await flushPromises()
    expect(calls).toHaveLength(0)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'r' }))
    await flushPromises()
    expect(calls[0]!.body).toEqual({ lead_id: 'lead-1', approve: false, final_text: '' })
    w.unmount()
  })
})

describe('CaptureCard', () => {
  it('shows why a post is not a lead and takes a verdict', async () => {
    const w = mount(CaptureCard, { props: { capture: capture(), threshold: 0.7 } })
    expect(w.text()).toContain('not a lead')
    expect(w.text()).toContain('intent 0.55')
    expect(w.text()).toContain('buy 0.55 < threshold 0.70')
    await w.get('[data-testid=label-buyer-cap-1]').trigger('click')
    expect(w.emitted('label')).toEqual([['buyer']])
    expect(w.find('[data-testid=rescore-cap-1]').exists()).toBe(true)
    const lead = mount(CaptureCard, { props: { capture: capture({ status: 'lead', status_reason: '' }), threshold: 0.7 } })
    expect(lead.find('[data-testid=rescore-cap-1]').exists()).toBe(false)
  })
})

describe('social store', () => {
  it('loads leads, captures, numbers and only the social settings', async () => {
    serve(
      on('GET', '/api/v1/social/leads?status=open', () => json(200, { leads: [lead({ lead_id: 'old', status: 'approved' }), lead()] })),
      on('GET', '/api/v1/social/captures?limit=100', () => json(200, { captures: [capture()] })),
      on('GET', '/api/v1/social/stats', () => json(200, stats())),
      on('GET', '/api/v1/system/config', () => json(200, { settings: [
        { key: 'safety.auto_pause', default: true, value: true },
        { key: 'social.intent_threshold', default: 0.7, value: 0.8 },
        { key: 'social.daily_budget_usd', default: 2, value: 3 },
      ] })),
    )
    const s = useSocial()
    await s.load()
    expect(s.leads.map((l) => l.lead_id)).toEqual(['lead-1', 'old'])
    expect(s.settings.map((x) => x.key)).toEqual(['social.intent_threshold', 'social.daily_budget_usd'])
    expect(s.threshold).toBe(0.8)
    expect(s.budget).toBe(3)
    expect(s.pending).toHaveLength(1)
  })
})
