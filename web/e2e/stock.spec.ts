// End-to-end: stock beyond filament on the running stack. Parts and packaging are added; a product's bill of
// materials says what a piece takes; the shelf is counted; an order of that product is served from the shelf (it
// skips printing and goes straight to QC); shipping it uses the packaging (not the parts: the piece was already
// made), drops the shelf below its minimum (Restock offered) and the box below its own (Reorder makes a PO).
import { fileURLToPath } from 'node:url'

import { type APIRequestContext, expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-5)
const SKU = `STK-${stamp.toUpperCase()}`
const MAGNET = `magnet-${stamp}`
const BOX = `box-${stamp}`
let orderId = ''

async function signIn(page: Page, path = '/live') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function send(page: Page, name: string, body: object, key?: string) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const r = await page.request.post(`/api/v1/commands/${name}`, {
    headers: { 'X-CSRF-Token': csrf, ...(key ? { 'Idempotency-Key': key } : {}) }, data: body })
  expect(r.status(), `${name}: ${await r.text()}`).toBe(202)
}

async function orderState(request: APIRequestContext, id: string) {
  const r = await request.get(`/api/v1/orders/${id}`)
  return r.ok() ? ((await r.json()) as { state: string }).state : 'missing'
}

async function addItem(page: Page, name: string, kind: string, onHand: string, min: string) {
  await page.getByTestId('add-item').click()
  const d = page.getByTestId('item-dialog')
  await d.getByTestId('item-name').fill(name)
  await d.getByTestId('item-kind').selectOption(kind)
  await d.getByTestId('item-on-hand').fill(onHand)
  await d.getByTestId('item-min').fill(min)
  await d.getByTestId('item-reorder').fill('50')
  await d.getByTestId('item-cost').fill('1.20')
  await d.getByTestId('save-item').click()
}

test('parts and packaging, and a product that takes them', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page, '/filament?tab=items')
  await addItem(page, `Magnet ${stamp}`, 'part', '100', '10')
  await expect(page.getByTestId(`item-on-hand-${MAGNET}`)).toContainText('100', { timeout: 20_000 })
  await addItem(page, `Box ${stamp}`, 'packaging', '20', '20')
  await expect(page.getByTestId(`item-on-hand-${BOX}`)).toContainText('20', { timeout: 20_000 })

  // the product, and what one piece takes
  await page.goto('/catalog')
  await page.getByTestId('import-3mf').click()
  let dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('file').setInputFiles(fileURLToPath(new URL('./fixtures/slab-easel.3mf', import.meta.url)))
  await expect(dialog.getByTestId('upload-info')).toContainText('sliced')
  await dialog.getByTestId('sku').fill(SKU)
  await dialog.getByTestId('name').fill(`Magnet frame ${stamp}`)
  await dialog.getByTestId('price').fill('60')
  await dialog.getByTestId('save-product').click()
  await expect(page.getByTestId(`sku-${SKU}`)).toBeVisible({ timeout: 20_000 })
  await page.getByTestId(`sku-${SKU}`).getByRole('button', { name: `Edit ${SKU}` }).click()
  dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('bom-add').click()
  await dialog.getByTestId('bom-item-0').selectOption(MAGNET)
  await dialog.getByTestId('bom-qty-0').fill('2')
  await dialog.getByTestId('bom-add').click()
  await dialog.getByTestId('bom-item-1').selectOption(BOX)
  await dialog.getByTestId('bom-qty-1').fill('1')
  await expect(dialog.getByTestId('bom')).toContainText('₪3.60 per piece')
  await dialog.getByTestId('save-product').click()
  await expect(dialog).toBeHidden()                      // saved: the product, then its parts per piece
  await page.goto('/filament?tab=items')
  await expect(page.getByTestId(`item-${MAGNET}`)).toContainText(`${SKU} ×2`, { timeout: 20_000 })
})

test('a counted shelf serves an order: no printing, straight to QC', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page, '/filament?tab=shelf')
  await page.getByTestId('shelf-all').check()
  await page.getByTestId(`shelf-edit-${SKU}`).click()
  const d = page.getByTestId('shelf-dialog')
  await d.getByTestId('shelf-min').fill('2')
  await d.getByTestId('shelf-count').fill('2')
  await d.getByTestId('shelf-save').click()
  await expect(page.getByTestId(`shelf-free-${SKU}`)).toHaveText('2', { timeout: 20_000 })
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m4-shelf.png`, fullPage: true })

  const key = `e2e${stamp}stock`                       // the order id is ord-<first 8 characters>: unique per run
  orderId = `ord-${key.slice(0, 8)}`
  await send(page, 'orders.create', { customer_name: 'Shelf customer', items: [{ product: 'catalog', sku: SKU, quantity: 1 }] }, key)
  await expect.poll(() => orderState(page.request, orderId), { timeout: 30_000 }).toBe('QC')
  // the shelf: one piece reserved, one free, now below the minimum of 2 -> a restock is offered
  await page.reload()
  await expect(page.getByTestId(`shelf-${SKU}`)).toContainText('1', { timeout: 20_000 })
  await expect(page.getByTestId(`shelf-free-${SKU}`)).toHaveText('1')
  await expect(page.getByTestId(`restock-${SKU}`)).toBeVisible()
  await page.goto('/catalog')
  await expect(page.getByTestId(`on-shelf-${SKU}`)).toContainText('1 on the shelf')
})

test('shipping uses the packaging, not the parts; the low box is reordered', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page, '/orders')
  await send(page, 'orders.qc', { order_id: orderId, passed: true })
  await expect.poll(async () => (await (await page.request.get(`/api/v1/orders/${orderId}`)).json()).qc, { timeout: 20_000 }).toBe('passed')
  await send(page, 'orders.ship', { order_id: orderId, carrier: 'pickup' })
  await expect.poll(() => orderState(page.request, orderId), { timeout: 20_000 }).toBe('SHIPPED')

  await page.goto('/filament?tab=items')
  await expect(page.getByTestId(`item-on-hand-${BOX}`)).toContainText('19', { timeout: 20_000 })
  await expect(page.getByTestId(`item-on-hand-${MAGNET}`)).toContainText('100')    // made earlier: no parts now
  await page.getByTestId(`reorder-item-${BOX}`).click()
  await page.locator('dialog.confirm').getByRole('button', { name: 'Create order' }).click()
  await page.getByRole('tab', { name: 'Filament' }).click()
  await expect(page.getByTestId('purchase-orders')).toContainText(`Box ${stamp} × 50`, { timeout: 20_000 })
  await expect(page.getByTestId('condition')).toBeVisible()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m4-filament.png`, fullPage: true })

  // the shelf after the sale: one piece left, nothing reserved
  await page.getByRole('tab', { name: 'Finished goods' }).click()
  await page.getByTestId('shelf-all').check()
  await expect(page.getByTestId(`shelf-free-${SKU}`)).toHaveText('1')
})
