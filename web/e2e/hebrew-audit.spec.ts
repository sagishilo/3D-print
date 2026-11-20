// A tool, not a check (runs only with HEBREW_AUDIT=1): every screen in Hebrew, and the visible text that is still
// English, written to the file HEBREW_AUDIT names. Use it to find words the dictionary misses after a change.
import { writeFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'
const OUT = process.env.HEBREW_AUDIT ?? ''

test.skip(!PASSWORD || !OUT, 'set HEBREW_AUDIT=<file> to run the Hebrew audit')

const PATHS = ['/today', '/social', '/catalog', '/niches', '/pricing', '/store', '/live', '/orders', '/fleet', '/schedule',
  '/filament', '/filament?tab=shelf', '/filament?tab=items', '/finance', '/finance?tab=income', '/finance?tab=expenses',
  '/finance?tab=products', '/finance?tab=receivables', '/finance?tab=documents', '/finance?tab=vat', '/finance?tab=settings',
  '/scorecard', '/system', '/system?tab=agents', '/system?tab=nats', '/system?tab=dlq', '/system?tab=audit',
  '/integrations', '/users', '/help']

test('list the English left on every screen in Hebrew', async ({ page }) => {
  test.setTimeout(300_000)
  await page.goto('/today')
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.locator('form button[type="submit"]').click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const lang = (l: string) => page.request.patch('/api/v1/auth/prefs', { headers: { 'X-CSRF-Token': csrf }, data: { lang: l } })
  await lang('he')
  await page.evaluate(() => localStorage.setItem('pf-lang', 'he'))
  const found = new Map<string, Set<string>>()
  for (const path of PATHS) {
    await page.goto(path)
    await page.waitForTimeout(2500)
    const texts = await page.evaluate(() => {
      const out: string[] = []
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const el = n.parentElement
        if (!el || el.closest('[translate="no"], code, pre, .mono, script, style') || !el.offsetParent) continue
        const s = (n.nodeValue ?? '').replace(/\s+/g, ' ').trim()
        if (/[A-Za-z]{3}/.test(s)) out.push(s)
      }
      for (const el of document.querySelectorAll('[placeholder], [title], [aria-label]')) {
        for (const a of ['placeholder', 'title', 'aria-label']) {
          const v = el.getAttribute(a)
          if (v && /[A-Za-z]{3}/.test(v) && !/[֐-׿]/.test(v)) out.push(`@${a}: ${v}`)
        }
      }
      return out
    })
    for (const s of texts) {
      if (!found.has(s)) found.set(s, new Set())
      found.get(s)!.add(path)
    }
  }
  await lang('en')
  writeFileSync(OUT, [...found].map(([s, p]) => `${s}\t${[...p].join(' ')}`).join('\n'), 'utf8')
})
