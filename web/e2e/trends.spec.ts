// End-to-end: the niche scorecard and the Trend Analyst on the running stack (the dev stack runs it with
// TREND_LLM=fake). The scorecard shows the niches with the sales other tests made; CSV and Excel download; with the
// insights setting on, a rebuild writes a nightly-style insight; the Social page shows the Trends panel.
import { expect, type Page, test } from '@playwright/test'

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

async function setting(page: Page, key: string, value: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const r = await page.request.post('/api/v1/commands/config.set', { headers: { 'X-CSRF-Token': csrf }, data: { key, value } })
  expect(r.status()).toBe(202)
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  await setting(page, 'trend.enabled', false)
  await page.close()
})

test('the scorecard compares the niches and downloads as CSV and Excel', async ({ page }) => {
  await signIn(page, '/scorecard')
  await expect(page.getByRole('heading', { name: 'Niche scorecard' })).toBeVisible()
  const gifts = page.getByTestId('sc-gifts')                         // nameplates sold by the other tests
  await expect(gifts).toBeVisible({ timeout: 20_000 })
  await expect(gifts).toContainText('Per print hour')
  const csv = await page.request.get('/api/v1/analytics/scorecard?days=30&format=csv')
  expect(csv.status()).toBe(200)
  const text = await csv.text()
  expect(text.charCodeAt(0)).toBe(0xfeff)                            // a BOM: Excel shows Hebrew
  expect(text.split('\n')[0]).toContain('revenue ₪')
  const xlsx = await page.request.get('/api/v1/analytics/scorecard?days=30&format=xlsx')
  expect(xlsx.headers()['content-type']).toContain('spreadsheetml')
  expect((await xlsx.body()).subarray(0, 2).toString()).toBe('PK')   // a zip: an .xlsx workbook
  const daily = await page.request.get('/api/v1/analytics/daily?days=30&by=sku&format=csv')
  expect(daily.status()).toBe(200)
  await expect(page.getByTestId('daily-sku-xlsx')).toHaveAttribute('href', '/api/v1/analytics/daily?days=30&by=sku&format=xlsx')
})

test('with insights on, a rebuild writes an insight per niche', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page, '/scorecard')
  await setting(page, 'trend.enabled', true)
  await page.waitForTimeout(6_000)                                    // agents read the settings every 5 seconds
  await page.getByTestId('sc-rebuild').click()
  await page.locator('dialog.confirm').getByRole('button', { name: 'Rebuild' }).click()
  await expect.poll(async () => {
    await page.reload()
    return page.locator('[data-testid^="sc-insight-"]').count()
  }, { timeout: 90_000, intervals: [3_000] }).toBeGreaterThan(0)
  await expect(page.locator('[data-testid^="sc-insight-"]').first()).toContainText('יחידות')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m6-scorecard.png`, fullPage: true })
})

test('the Social page shows the Trends panel', async ({ page }) => {
  await signIn(page, '/social')
  const panel = page.getByTestId('trend-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('Trend Analyst')
  await expect(panel.getByRole('link', { name: /niche scorecard/ })).toBeVisible()
})
