// Social Listening (ADR-0007): posts captured by group members, scored by the social agent, leads with a Hebrew draft
// that a person edits, approves (copy), posts themselves and marks posted. Pure helpers; the store is stores/social.ts.
import { ago } from './format'
import type { Tone } from './types'

export type CaptureStatus = 'new' | 'scoring' | 'ignored' | 'scored' | 'lead' | 'duplicate' | 'waiting' | 'failed'
export type LeadStatus = 'drafting' | 'pending' | 'approved' | 'posted' | 'dismissed' | 'failed'
export type Label = 'buyer' | 'not_buyer'

export interface Capture {
  capture_id: string
  group_name: string
  post_url: string
  post_text: string
  captured_by: string
  captured_at: string
  received_at: string
  status: CaptureStatus
  status_reason: string
  prefilter: number | null
  intent: string | null
  intent_score: number | null
  sku_guess: string | null
  summary: string
  label: Label | null
  labelled_by: string | null
}

export interface Lead {
  lead_id: string
  capture_id: string
  intent_score: number
  sku: string | null
  sku_name: string | null
  sku_price_agorot: number | null
  summary: string
  draft: string
  draft_notes: string
  final_text: string | null
  status: LeadStatus
  status_reason: string
  created_at: string
  decided_by: string | null
  decided_at: string | null
  posted_by: string | null
  posted_at: string | null
  group_name: string
  post_url: string
  post_text: string
  captured_by: string
  captured_at: string
  intent: string | null
}

export interface Accuracy {
  labelled: number
  true_leads: number
  false_leads: number
  missed: number
  correctly_ignored: number
  precision: number | null
  recall: number | null
  agreement: number | null
}

export interface SocialStats {
  captures_7d: Partial<Record<CaptureStatus, number>>
  captures_today: number
  groups_30d: number
  last_capture: string | null
  leads: Partial<Record<LeadStatus, number>>
  posted_7d: number
  spend_month: { model: string; calls: number; usd: number; usd_today: number; problems: number }[]
  spend_today_usd: number
  accuracy: Accuracy
  models: { classify: string; draft: string }
  agent: { online: boolean; llm: string | null; embedder: string | null; at: string | null }
}

export interface CaptureToken {
  token_id: string
  label: string
  created_by: string
  created_at: string
  last_used_at: string | null
  uses: number
  revoked_at: string | null
}

export const STATUS_LABEL: Record<CaptureStatus, string> = {
  new: 'queued',
  scoring: 'scoring…',
  ignored: 'ignored',
  scored: 'not a lead',
  lead: 'lead',
  duplicate: 'duplicate',
  waiting: 'waiting',
  failed: 'failed',
}

export const STATUS_TONE: Record<CaptureStatus, Tone> = {
  new: 'idle',
  scoring: 'accent',
  ignored: 'idle',
  scored: 'idle',
  lead: 'ok',
  duplicate: 'idle',
  waiting: 'warn',
  failed: 'danger',
}

export const LEAD_LABEL: Record<LeadStatus, string> = {
  drafting: 'drafting…',
  pending: 'Draft ready',
  approved: 'Approved · paste it',
  posted: 'Posted',
  dismissed: 'Rejected',
  failed: 'failed',
}

/** Can the capture be scored again (it waited, failed, or a person wants a second opinion)? */
export const canRescore = (c: Pick<Capture, 'status'>) => ['waiting', 'failed', 'ignored', 'scored'].includes(c.status)

export function intentTone(score: number | null | undefined, threshold: number): Tone {
  if (score == null) return 'idle'
  if (score >= threshold) return 'ok'
  if (score >= threshold - 0.2) return 'warn'
  return 'idle'
}

export const score = (n: number | null | undefined) => (n == null ? '–' : n.toFixed(2))

/** The page subtitle (ADR-0007 wording: captures made by people, never "monitored" or "scanned"). */
export function captureLine(s: Pick<SocialStats, 'groups_30d' | 'last_capture'> | null, now = Date.now()): string {
  if (!s || !s.last_capture) return 'No captures yet: capture a post with the browser extension, or Add post'
  const groups = `${s.groups_30d} group${s.groups_30d === 1 ? '' : 's'}`
  return `Captures from ${groups} (30 days) · last capture ${ago(Date.parse(s.last_capture), now)} ago`
}

export function modelLine(s: SocialStats | null): string {
  if (!s) return ''
  if (!s.agent.online) return 'Social agent offline'
  if (s.agent.llm === 'none') return 'No ANTHROPIC_API_KEY: captures wait (set it in .env, restart social, then Rescore)'
  if (s.agent.llm === 'fake') return 'Test mode: keyword triage, no Claude calls'
  return `Triage ${s.models.classify} · drafts ${s.models.draft}`
}

export const usd = (n: number) => (n < 0.01 && n > 0 ? '< $0.01' : `$${n.toFixed(2)}`)

export function spendLine(s: SocialStats | null, budget: number): string {
  if (!s) return ''
  const month = s.spend_month.reduce((a, r) => a + r.usd, 0)
  return `${usd(s.spend_today_usd)} today of ${usd(budget)} · ${usd(month)} this month`
}

export function accuracyLine(a: Accuracy | null | undefined): string {
  if (!a || !a.labelled) return 'Label captures (Buyer / Not buyer) to measure how well leads are picked'
  const parts = [`${a.labelled} labelled`]
  parts.push(a.precision == null ? 'no leads among them yet' : `${Math.round(a.precision * 100)}% of leads were buyers`)
  parts.push(a.recall == null ? 'no buyers among them yet' : `${Math.round(a.recall * 100)}% of buyers became leads`)
  return parts.join(' · ')
}

/** The reply that will be copied: the person's edit, else the draft. */
export const replyText = (lead: Pick<Lead, 'draft' | 'final_text'>, edited?: string | null) =>
  (edited ?? lead.final_text ?? lead.draft ?? '').trim()

/** Open leads first (pending, then approved), newest first inside each. */
export function sortLeads<T extends Pick<Lead, 'status' | 'created_at'>>(leads: T[]): T[] {
  const rank: Record<string, number> = { pending: 0, drafting: 1, approved: 2, posted: 3, dismissed: 4, failed: 5 }
  return [...leads].sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || (a.created_at < b.created_at ? 1 : -1))
}

/** CSV of leads for "Export leads" (UTF-8 BOM so Excel shows Hebrew). */
export function leadsCsv(leads: Lead[]): string {
  const cell = (v: unknown) => {
    const s = v == null ? '' : String(v)
    return /[",\n\r]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s
  }
  const head = ['lead_id', 'created_at', 'status', 'intent_score', 'sku', 'group', 'post_url', 'post', 'reply', 'posted_at']
  const rows = leads.map((l) => [l.lead_id, l.created_at, l.status, l.intent_score.toFixed(2), l.sku ?? '', l.group_name,
    l.post_url, l.post_text, replyText(l), l.posted_at ?? ''].map(cell).join(','))
  return '﻿' + [head.join(','), ...rows].join('\r\n') + '\r\n'
}

/** Copy text; falls back to a hidden textarea where the async clipboard is not allowed. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    let ok = false
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    ta.remove()
    return ok
  }
}
