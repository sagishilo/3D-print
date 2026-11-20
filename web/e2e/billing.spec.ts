// End-to-end: W2-B on the running stack. Morning and Israel Post in test mode. A product on the shelf is ordered (it
// skips printing: straight to QC); its payment gets a test tax document (number and preview on the order and in
// Finance > Documents); "Pass & ship" passes QC and makes an Israel Post test parcel to the typed address: the order
// is Shipped with its tracking link and printable label; the parcel is moved to Delivered by hand. Both connectors are
// switched off again at the end (other tests ship with a typed carrier).
import { fileURLToPath } from 'node:url'

import { type APIRequestContext, expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-5)
const SKU = `BIL-${stamp.toUpperCase()}`
const key = `e2e${stamp}bill`
const orderId = `ord-${key.slice(0, 8)}`

async function signIn(page: Page, path = '/today') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function send(page: Page, name: string, body: object, idem?: string) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const r = await page.request.post(`/api/v1/commands/${name}`, {
    headers: { 'X-CSRF-Token': csrf, ...(idem ? { 'Idempotency-Key': idem } : {}) }, data: body })
  expect(r.status(), `${name}: ${await r.text()}`).toBe(202)
}

async function detail(request: APIRequestContext) {
  const r = await request.get(`/api/v1/orders/${orderId}`)
  return r.ok() ? ((await r.json()) as Record<string, any>) : null
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  for (const id of ['morning', 'israel-post']) await send(page, 'integrations.configure', { connector_id: id, enabled: false })
  await page.close()
})

test('a paid order gets its document; Pass & ship makes an Israel Post parcel', async ({ page }) => {
  test.setTimeout(150_000)
  await signIn(page, '/catalog')
  await send(page, 'integrations.configure', { connector_id: 'morning', enabled: true, mode: 'fake' })
  await send(page, 'integrations.configure', { connector_id: 'israel-post', enabled: true, mode: 'fake',
    settings: { sender_name: 'PrintFarm e2e', sender_city: 'Haifa 3100001' } })

  // a product with two pieces on the shelf
  await page.getByTestId('import-3mf').click()
  const dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('file').setInputFiles(fileURLToPath(new URL('./fixtures/slab-easel.3mf', import.meta.url)))
  await expect(dialog.getByTestId('upload-info')).toContainText('sliced')
  await dialog.getByTestId('sku').fill(SKU)
  await dialog.getByTestId('name').fill(`Billing frame ${stamp}`)
  await dialog.getByTestId('price').fill('60')
  await dialog.getByTestId('save-product').click()
  await expect(page.getByTestId(`sku-${SKU}`)).toBeVisible({ timeout: 20_000 })
  await send(page, 'stock.count', { sku: SKU, on_hand: 2 })

  // the order is served from the shelf: straight to QC; its payment gets a test document
  await send(page, 'orders.create', { customer_name: 'Ship E2E', customer_ref: 'ship@example.com',
    items: [{ product: 'catalog', sku: SKU, quantity: 1 }] }, key)
  await expect.poll(async () => (await detail(page.request))?.state, { timeout: 40_000 }).toBe('QC')
  await send(page, 'payments.record', { order_id: orderId, amount_agorot: 6000, method: 'bit' })
  await expect.poll(async () => (await detail(page.request))?.documents?.[0]?.status, { timeout: 30_000 }).toBe('issued')

  await page.goto(`/orders?order=${orderId}`)
  const drawer = page.getByTestId('order-drawer')
  await expect(drawer.locator('[data-testid^="doc-"]').first()).toContainText(/(Tax invoice-receipt|Receipt) 5\d{4}/)
  const docUrl = (await detail(page.request))!.documents[0].url as string
  const preview = await page.request.get(docUrl)
  expect(preview.ok()).toBe(true)
  expect(await preview.text()).toContain('Ship E2E')

  // one click: QC passes, then the parcel is made with Israel Post (test) to the typed address
  await drawer.getByTestId('ship-open').click()                       // folded until QC has passed
  await expect(drawer.getByTestId('ship-courier')).toHaveValue('israel-post')
  await drawer.getByTestId('ship-street').fill('Herzl 1')
  await drawer.getByTestId('qc-pass-ship').click()
  const confirmDialog = page.locator('dialog.confirm')
  if (await confirmDialog.isVisible().catch(() => false)) await confirmDialog.getByRole('button', { name: 'Pass anyway' }).click()
  await expect(drawer.getByTestId('parcel-status')).toHaveText('Label made', { timeout: 40_000 })
  await expect.poll(async () => (await detail(page.request))?.state, { timeout: 20_000 }).toBe('SHIPPED')
  const tracking = drawer.getByTestId('parcel-tracking')
  await expect(tracking).toHaveText(/^RR\d{9}IL$/)
  expect(await tracking.getAttribute('href')).toMatch(/^https:\/\/israelpost\.co\.il\/itemtrace\?itemcode=RR\d{9}IL$/)
  const labelHref = await drawer.getByTestId('parcel-label').getAttribute('href')
  const label = await (await page.request.get(labelHref!)).text()
  expect(label).toContain('Herzl 1')
  expect(label).toContain('PrintFarm e2e')
  if (SHOTS) await drawer.screenshot({ path: `${SHOTS}/w2-b-order-shipped.png` })

  // where it is: moved to Delivered by hand; the board card shows it
  await drawer.getByTestId('parcel-delivered').click()
  await expect(drawer.getByTestId('parcel-status')).toHaveText('Delivered', { timeout: 20_000 })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId(`card-parcel-${orderId}`)).toHaveText('Delivered', { timeout: 20_000 })

  // Finance > Documents lists it
  await page.goto('/finance?tab=documents')
  const pid = (await detail(page.request))!.payments[0].payment_id as string
  await expect(page.getByTestId(`document-${pid}`)).toContainText('(test document)')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/w2-b-documents.png`, fullPage: true })
})
