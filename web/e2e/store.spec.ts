// End-to-end: W2-A on the running stack. Shopify in test mode (a fresh batch of three test orders) syncs; its
// products wait in "Map this"; mapping them on the Store orders screen imports the paid orders into the Order
// Pipeline (channel shopify, paid), once; the unpaid one waits and is skipped. The connector's settings show on
// Integrations. At the end the imported orders are cancelled and the connector is switched off again.
import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const BATCH = 100_000 + (Date.now() % 9_000_000)           // a batch no earlier run used
const ext = (n: number) => `F${BATCH}-${n}`
const KEYS = ['NAMEPLATE-BLACK', 'NAMEPLATE-WHITE', 'GIFT-WRAP']

async function signIn(page: Page, path = '/store') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function send(page: Page, name: string, body: object) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const r = await page.request.post(`/api/v1/commands/${name}`, { headers: { 'X-CSRF-Token': csrf }, data: body })
  expect(r.status(), `${name}: ${await r.text()}`).toBe(202)
}

const storeStatus = async (page: Page, id: string) => {
  const r = (await (await page.request.get('/api/v1/store/orders')).json()) as { orders: { external_id: string; status: string }[] }
  return r.orders.find((o) => o.external_id === id)?.status ?? 'missing'
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page, '/today')
  for (const n of [1, 2]) {
    const r = await page.request.get(`/api/v1/orders/shp-${ext(n)}`)
    if (r.ok() && !['CANCELLED', 'SHIPPED'].includes(((await r.json()) as { state: string }).state))
      await send(page, 'orders.cancel', { order_id: `shp-${ext(n)}` })
  }
  await send(page, 'integrations.configure', { connector_id: 'shopify', enabled: false })
  await page.close()
})

test('Shopify test orders wait for a mapping, then come in once', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page, '/integrations')
  // a clean start: no mapping of the test products, a fresh batch, the connector on in test mode
  for (const k of KEYS) await send(page, 'integrations.map_sku', { connector_id: 'shopify', external_key: k, remove: true })
  await send(page, 'integrations.configure', { connector_id: 'shopify', enabled: true, mode: 'fake', settings: { fake_batch: BATCH } })
  await expect(page.getByTestId('settings-shopify')).toBeVisible()
  await expect(page.getByTestId('setting-shopify-shop')).toBeVisible()
  await expect(page.getByTestId('secret-SHOPIFY_CLIENT_ID')).toContainText(/set|missing/)
  await expect.poll(() => storeStatus(page, ext(1)), { timeout: 30_000 }).toBe('needs_mapping')
  expect(await storeStatus(page, ext(3))).toBe('waiting_payment')

  await page.goto('/store')
  const black = page.getByTestId('map-NAMEPLATE-BLACK')
  await expect(black).toContainText(`#${BATCH}001`)
  await expect(black).toContainText('The customer filled in: Name')
  await expect(page.getByTestId(`status-${ext(1)}`)).toHaveText('Waits for you')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/w2-a-store-queue.png`, fullPage: true })

  // a nameplate (prefilled: black, text from "Name"), then white, then gift wrap is not printed
  await page.getByTestId('map-save-NAMEPLATE-BLACK').click()
  await expect(page.getByTestId(`status-${ext(1)}`)).toHaveText('Imported', { timeout: 20_000 })
  await expect(page.getByTestId(`ours-${ext(1)}`)).toHaveText(`shp-${ext(1)}`)
  await page.getByTestId('map-save-NAMEPLATE-WHITE').click()
  await expect(page.getByTestId('map-GIFT-WRAP')).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('map-product-GIFT-WRAP').selectOption('ignore')
  await page.getByTestId('map-save-GIFT-WRAP').click()
  await expect(page.getByTestId(`status-${ext(2)}`)).toHaveText('Imported', { timeout: 20_000 })
  await expect(page.getByTestId('queue-empty')).toBeVisible()
  await expect(page.getByTestId('skumap-GIFT-WRAP')).toContainText('not printed')

  // the unpaid one is skipped (asked first)
  await page.getByTestId(`skip-${ext(3)}`).click()
  await page.locator('dialog.confirm').getByRole('button', { name: 'Skip' }).click()
  await expect(page.getByTestId(`status-${ext(3)}`)).toHaveText('Skipped', { timeout: 20_000 })
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/w2-a-store-orders.png`, fullPage: true })

  // in the Order Pipeline: the shopify channel, paid, the customer's text on the nameplate
  await expect.poll(async () => (await page.request.get(`/api/v1/orders/shp-${ext(2)}`)).status(), { timeout: 20_000 }).toBe(200)
  const o = (await (await page.request.get(`/api/v1/orders/shp-${ext(2)}`)).json()) as Record<string, any>
  expect(o.channel).toBe('shopify')
  expect(o.total_agorot).toBe(12300)
  expect(JSON.stringify(o.items)).toContain('Dan')
  await expect.poll(async () => {
    const r = (await (await page.request.get(`/api/v1/orders/shp-${ext(2)}`)).json()) as Record<string, any>
    return Number(r.paid_agorot ?? r.payments?.reduce?.((a: number, p: { amount_agorot: number }) => a + p.amount_agorot, 0) ?? 0)
  }, { timeout: 20_000 }).toBe(12300)

  // a second sync of the same orders changes nothing
  await send(page, 'integrations.run', { connector_id: 'shopify' })
  await page.waitForTimeout(3_000)
  const all = (await (await page.request.get('/api/v1/store/orders')).json()) as { orders: { external_id: string }[] }
  expect(all.orders.filter((x) => x.external_id.startsWith(`F${BATCH}-`))).toHaveLength(3)
})

test('the phone layout of Store orders fits the screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await expect(page.getByTestId('store-orders')).toBeVisible()
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(392)
})
