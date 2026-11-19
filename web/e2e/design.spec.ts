// End-to-end: display preferences (theme, density, Simple / Expert) from the user menu, saved per person and kept
// after a reload; Simple mode hides the technical panels. Also screenshots every screen in light + compact and dark.
import { expect, type Page, test } from '@playwright/test'

import { NAV } from '../src/nav'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

async function signIn(page: Page, path = '/live') {
  await page.goto(path)
  await page.locator('#user').fill('admin')
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function choose(page: Page, testid: string) {
  if (!(await page.getByTestId('display-prefs').isVisible())) await page.getByTestId('user-menu').click()
  await page.getByTestId(testid).click()
  await expect(page.getByTestId(testid)).toHaveAttribute('aria-checked', 'true')
}

const html = (page: Page, attr: string) => page.evaluate((a) => document.documentElement.getAttribute(a), attr)
const bg = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor)

/** Back to the defaults for admin (an owner): other browser tests expect them. */
async function reset(page: Page) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  await page.request.patch('/api/v1/auth/prefs', { headers: { 'X-CSRF-Token': csrf },
    data: { theme: 'system', density: 'comfortable', mode: 'expert' } })
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  await reset(page)
  await page.close()
})

test('theme: light and dark are applied at once and kept after a reload; System follows the computer', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await signIn(page)
  await choose(page, 'theme-light')
  expect(await html(page, 'data-theme')).toBe('light')
  expect(await bg(page)).toBe('rgb(244, 246, 249)')
  await page.reload()
  await expect(page.getByTestId('user-menu')).toBeVisible()
  await expect.poll(() => html(page, 'data-theme')).toBe('light')        // saved on the gateway, not in this browser
  await choose(page, 'theme-dark')
  expect(await bg(page)).toBe('rgb(11, 13, 16)')
  await choose(page, 'theme-system')
  expect(await html(page, 'data-theme')).toBeNull()
  expect(await bg(page)).toBe('rgb(11, 13, 16)')                         // this "computer" prefers dark
  await page.emulateMedia({ colorScheme: 'light' })
  expect(await bg(page)).toBe('rgb(244, 246, 249)')
})

test('density: compact makes rows and controls smaller', async ({ page }) => {
  await signIn(page)
  const height = () => page.getByTestId('pause-all').evaluate((el) => el.getBoundingClientRect().height)
  await choose(page, 'density-comfortable')
  expect(await height()).toBeCloseTo(40, 0)
  await choose(page, 'density-compact')
  expect(await html(page, 'data-density')).toBe('compact')
  expect(await height()).toBeCloseTo(34, 0)
  await page.reload()
  await expect.poll(() => html(page, 'data-density')).toBe('compact')
  await choose(page, 'density-comfortable')
})

test('Simple mode hides the technical panels; Expert shows everything', async ({ page }) => {
  await signIn(page, '/system')
  await choose(page, 'mode-expert')
  await page.keyboard.press('Escape')
  for (const tab of ['Agents', 'NATS', 'Config']) await expect(page.getByRole('tab', { name: tab })).toBeVisible()
  await expect(page.getByRole('tab', { name: /^DLQ/ })).toBeVisible()
  await expect(page.getByTestId('nav-agents')).toBeVisible()
  await expect(page.getByTestId('settings')).toBeVisible()

  await choose(page, 'mode-simple')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('simple-health')).toBeVisible()
  for (const tab of ['Agents', 'NATS', 'Config']) await expect(page.getByRole('tab', { name: tab })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: /^DLQ/ })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'Audit log' })).toBeVisible()        // not technical: owners keep it
  await expect(page.getByTestId('settings')).toHaveCount(0)
  await expect(page.getByTestId('nav-agents')).toHaveCount(0)
  await expect(page.getByTestId('backups')).toBeVisible()
  // a technical tab in the address opens the overview instead
  await page.goto('/system?tab=nats')
  await expect(page.getByTestId('simple-health')).toBeVisible()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m2-system-simple.png`, fullPage: true })
  await choose(page, 'mode-expert')
})

test('every screen in light + compact and in dark (screenshots for review)', async ({ page }) => {
  test.skip(!SHOTS, 'set PW_SCREENSHOTS to keep the screenshots')
  test.setTimeout(180_000)
  await signIn(page)
  for (const [theme, density] of [['light', 'compact'], ['dark', 'comfortable']] as const) {
    await choose(page, `theme-${theme}`)
    await choose(page, `density-${density}`)
    await page.keyboard.press('Escape')
    for (const item of [...NAV.map((n) => n.to), '/users', '/fleet/add', '/help']) {
      await page.goto(item)
      await expect(page.getByTestId('user-menu')).toBeVisible()
      await page.waitForTimeout(600)
      await page.screenshot({ path: `${SHOTS}/m2-${theme}-${item.replace(/\//g, '_')}.png`, fullPage: true })
    }
  }
  // Two rounds of every screen come close to the gateway's per-computer request limit (600 a minute): let the
  // window pass so the next test file starts fresh.
  await page.waitForTimeout(60_000)
})
