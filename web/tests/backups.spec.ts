// Backups panel on System: local and encrypted off-site status from GET /api/v1/system/backups.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BackupsPanel from '@/components/system/BackupsPanel.vue'

import { json, serve } from './helpers'

const recent = new Date(Date.now() - 3 * 3600_000).toISOString()

async function panel(body: unknown) {
  serve((c) => (c.url === '/api/v1/system/backups' ? json(200, body) : undefined))
  const w = mount(BackupsPanel)
  await flushPromises()
  return w
}

describe('BackupsPanel', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows a protected farm: a recent local copy and an encrypted off-site copy', async () => {
    const w = await panel({ known: true, state: 'ok', problems: [], keep: 14, offsite_enabled: true, offsite_remote: 'gdrive',
      offsite_keep_days: 90, local_ok_at: recent, local_bytes: 2_400_000, offsite_ok_at: recent, drill_ok_at: recent, drill_tables: 61 })
    expect(w.get('[data-testid="backup-state"]').text()).toBe('Protected')
    expect(w.get('[data-testid="backup-local"]').text()).toContain('3 h ago')
    expect(w.get('[data-testid="backup-offsite"]').text()).toContain('encrypted · gdrive')
    expect(w.get('[data-testid="backup-drill"]').text()).toContain('61 tables')
  })

  it('says when copies stay on this PC only, and how to practise a restore', async () => {
    const w = await panel({ known: true, state: 'local_only', problems: [], keep: 14, offsite_enabled: false, local_ok_at: recent })
    expect(w.get('[data-testid="backup-state"]').text()).toBe('Local only')
    expect(w.get('[data-testid="backup-offsite"]').text()).toContain('Not set up')
    expect(w.get('[data-testid="backup-drill"]').text()).toContain('backup drill')
  })

  it('lists the problems', async () => {
    const w = await panel({ known: true, state: 'problem', problems: ['The last off-site copy failed: upload failed: 401'],
      offsite_enabled: true, local_ok_at: recent })
    expect(w.get('[data-testid="backup-state"]').text()).toBe('Needs attention')
    expect(w.get('[role="alert"]').text()).toContain('upload failed: 401')
  })

  it('without any status yet', async () => {
    const w = await panel({ known: false, state: 'unknown', problems: ['No backup status yet: is the backup service running?'] })
    expect(w.get('[data-testid="backup-state"]').text()).toBe('No status')
    expect(w.find('dl').exists()).toBe(false)
  })
})
