// End-to-end: Today and the command palette on the running stack. Signing in lands on Today; a new lead shows as
// "Confirm the order of …", is confirmed from Today (through the confirm dialog) and disappears; the ⌘K palette,
// keyboard only, opens a new order and switches the theme.
import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-5)
const CUSTOMER = `Today customer ${stamp}`

async function signIn(page: Page, path = '/') {
  await page.goto(path)
  await page.locator('#user').fill('admin')
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  await page.request.patch('/api/v1/auth/prefs', { headers: { 'X-CSRF-Token': csrf }, data: { theme: 'system' } })
  await page.close()
})

test('signing in opens Today; a new lead is confirmed from it and disappears', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await expect(page).toHaveURL((url) => url.pathname === '/today')
  await expect(page.getByRole('heading', { name: 'Today', exact: true })).toBeVisible()

  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const key = `t${stamp}lead0000`
  const orderId = `ord-${key.slice(0, 8)}`
  const r = await page.request.post('/api/v1/commands/orders.create', {
    headers: { 'X-CSRF-Token': csrf, 'Idempotency-Key': key },
    data: { customer_name: CUSTOMER, as_lead: true, items: [{ product: 'nameplate', text: 'Today' }] },
  })
  expect(r.status()).toBe(202)
  const line = page.getByTestId(`today-item-lead:${orderId}`)
  await expect(line).toContainText(`Confirm the order of ${CUSTOMER}`, { timeout: 20_000 })     // the live feed reloads Today
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m7-today.png`, fullPage: true })
  await page.getByTestId(`today-act-lead:${orderId}`).click()
  await page.locator('dialog.confirm').getByRole('button', { name: 'Confirm' }).click()
  await expect(line).toHaveCount(0, { timeout: 20_000 })
  await expect.poll(async () => (await (await page.request.get(`/api/v1/orders/${orderId}`)).json()).state, { timeout: 20_000 })
    .not.toBe('LEAD')
})

test('the command palette works from the keyboard alone', async ({ page }) => {
  await signIn(page, '/live')
  await page.keyboard.press('Control+k')
  const palette = page.getByTestId('palette')
  await expect(palette).toBeVisible()
  await page.keyboard.type('new order')
  await expect(page.getByTestId('palette-item-new-order')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/orders/)
  await expect(page.getByTestId('new-order')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('new-order')).toHaveCount(0)

  await page.keyboard.press('Control+k')
  await page.keyboard.type('dark')
  await page.keyboard.press('Enter')
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('dark')

  // a click on the search box opens it too; Escape closes it
  await page.getByTestId('palette-open').click()
  await expect(palette).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(palette).toHaveCount(0)

  await page.keyboard.press('Control+k')
  await page.keyboard.type('bambu')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/fleet\?printer=bambu/)
})
