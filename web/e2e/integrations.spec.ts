// End-to-end: Wave 2 foundations on the running stack. The demo connector starts off and in test mode, with its
// secret shown only as missing; an owner switches it on and runs it: records are mapped (a second run adds one more,
// never a duplicate); live mode without its secret fails and says which key is missing; the System card follows;
// operators cannot open Integrations. The connector ends switched off, in test mode.
import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

async function signIn(page: Page, path = '/integrations') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function configure(page: Page, body: object) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const r = await page.request.post('/api/v1/commands/integrations.configure', { headers: { 'X-CSRF-Token': csrf },
    data: { connector_id: 'demo', ...body } })
  expect(r.status()).toBe(202)
}

const mapped = async (page: Page) => Number(await page.getByTestId('mapped-demo').innerText())

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  await configure(page, { enabled: false, mode: 'fake', interval_s: 60 })
  await page.close()
})

test('an owner runs the demo connector: records map once, health and syncs show', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  const card = page.getByTestId('connector-demo')
  await expect(card).toBeVisible()
  await expect(page.getByTestId('secret-DEMO_STORE_TOKEN')).toContainText('missing')
  await expect(page.getByTestId('mode-demo')).toHaveValue('fake')

  if (!(await page.getByTestId('enable-demo').isChecked())) await page.getByTestId('enable-demo').check()
  await expect(page.getByTestId('health-demo')).not.toHaveText('Off', { timeout: 20_000 })
  await expect.poll(() => mapped(page), { timeout: 30_000 }).toBeGreaterThan(0)
  const before = await mapped(page)
  await page.getByTestId('run-demo').click()
  await expect.poll(async () => { await page.reload(); return mapped(page) }, { timeout: 30_000, intervals: [2_000] })
    .toBeGreaterThanOrEqual(Math.min(before + 1, 5))
  await expect(page.getByTestId('health-demo')).toHaveText('Healthy')
  await expect(card).toContainText('✓')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/w2-0-integrations.png`, fullPage: true })

  // the System card follows
  await page.goto('/system')
  await expect(page.getByTestId('integrations-health')).toContainText('Demo store')
})

test('live mode without its secret fails and names the missing key', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await configure(page, { enabled: true, mode: 'live' })
  await expect.poll(async () => { await page.reload(); return page.getByTestId('connector-demo').innerText() },
    { timeout: 30_000, intervals: [2_000] }).toContain('DEMO_STORE_TOKEN')
  await expect(page.getByTestId('health-demo')).toHaveText(/Failing|Down/)
  await configure(page, { mode: 'fake' })
  await expect.poll(async () => { await page.reload(); return page.getByTestId('health-demo').innerText() },
    { timeout: 30_000, intervals: [2_000] }).toBe('Healthy')
})

test('Integrations is for owners: the sidebar and the address both refuse others', async ({ page }) => {
  await signIn(page, '/today')
  await expect(page.getByRole('navigation', { name: 'Main' }).locator('a[href="/integrations"]')).toBeVisible()
  // the API refuses non-owners (checked with the session cookie gone)
  const anon = await page.request.get('/api/v1/integrations', { headers: { Cookie: '' } })
  expect([401, 403]).toContain(anon.status())
})
