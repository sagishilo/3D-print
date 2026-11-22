// End-to-end: managing catalog products on the running stack. An imported 3MF shows its own preview picture; Edit
// replaces it with an uploaded picture; the switch on the card turns the product off and on (audited); a product
// nothing refers to is deleted after a confirmation (its picture goes too); a product in a stock order cannot be
// deleted and is switched off instead; and the controls read in Hebrew, right to left.
import { fileURLToPath } from 'node:url'

import { type APIRequestContext, expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-5).toUpperCase()
const SKU = `PIC-${stamp}`                 // deleted at the end
const KEPT = `KEPT-${stamp}`               // goes into a stock order: can never be deleted
const fixture = (name: string) => fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url))
let batchOrder = ''

async function signIn(page: Page, path = '/catalog') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.locator('button[type=submit]').click()          // its text is translated in Hebrew
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function send(page: Page, name: string, body: object, key?: string) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  return page.request.post(`/api/v1/commands/${name}`, {
    headers: { 'X-CSRF-Token': csrf, ...(key ? { 'Idempotency-Key': key } : {}) }, data: body })
}

async function importProduct(page: Page, sku: string, name: string) {
  await page.getByTestId('import-3mf').click()
  const dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('file').setInputFiles(fixture('easel-with-preview.3mf'))
  await expect(dialog.getByTestId('upload-info')).toContainText('sliced')
  await dialog.getByTestId('sku').fill(sku)
  await dialog.getByTestId('name').fill(name)
  await dialog.getByTestId('price').fill('29')
  await dialog.getByTestId('save-product').click()
  await expect(page.getByTestId(`sku-${sku}`)).toBeVisible({ timeout: 20_000 })
}

/** The picture the browser actually loaded (not a broken image). */
async function loaded(page: Page, sku: string): Promise<number> {
  const img = page.getByTestId(`sku-image-${sku}`)
  await expect(img).toBeVisible({ timeout: 20_000 })
  return img.evaluate((el) => (el as HTMLImageElement).complete ? (el as HTMLImageElement).naturalWidth : 0)
}

async function audit(request: APIRequestContext, subject: string) {
  const r = await request.get(`/api/v1/system/audit?action=${encodeURIComponent(subject)}&limit=20`)
  return ((await r.json()) as { entries: { subject: string; payload: { sku?: string; active?: boolean } }[] }).entries
}

test.afterAll(async ({ browser }) => {
  // the stock order is cancelled; the product it refers to stays (switched off), as the rules want
  if (!batchOrder) return
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  await send(page, 'orders.cancel', { order_id: batchOrder, reason: 'e2e cleanup' })
  await page.close()
})

test('an imported 3MF shows its own preview picture', async ({ page }) => {
  await signIn(page)
  await page.getByText('switched off too').locator('input').check()
  await importProduct(page, SKU, `Picture easel ${stamp}`)
  expect(await loaded(page, SKU)).toBe(64)                          // the 64 × 48 plate render inside the fixture
  await page.getByTestId(`sku-${SKU}`).getByRole('button', { name: `Edit ${SKU}` }).click()
  await expect(page.getByTestId('product-image-source')).toHaveText('from the 3MF preview')
  await page.getByTestId('product-dialog').getByRole('button', { name: 'Cancel' }).click()
})

test('Edit replaces it with an uploaded picture', async ({ page }) => {
  await signIn(page)
  const card = page.getByTestId(`sku-${SKU}`)
  const before = await page.getByTestId(`sku-image-${SKU}`).getAttribute('src')
  await card.getByRole('button', { name: `Edit ${SKU}` }).click()
  const dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('image-file').setInputFiles(fixture('product-photo.png'))
  await expect(dialog.getByTestId('product-image-source')).toHaveText('New picture: kept when you save')
  await dialog.getByTestId('save-product').click()
  await expect(dialog).toBeHidden()
  await expect.poll(() => page.getByTestId(`sku-image-${SKU}`).getAttribute('src'), { timeout: 20_000 }).not.toBe(before)
  expect(await loaded(page, SKU)).toBe(80)                          // the 80 × 60 picture
  await card.getByRole('button', { name: `Edit ${SKU}` }).click()
  await expect(page.getByTestId('product-image-source')).toHaveText('uploaded picture')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/catalog-edit-picture.png`, fullPage: true })
  await page.getByTestId('product-dialog').getByRole('button', { name: 'Cancel' }).click()
})

test('the switch on the card turns the product off and on again, audited', async ({ page }) => {
  await signIn(page)
  const sw = page.getByTestId(`active-${SKU}`)
  const sale = page.getByTestId(`sale-${SKU}`)
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  await sw.click()
  await expect(sale).toContainText('Switched off', { timeout: 20_000 })
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await expect(page.getByTestId(`add-${SKU}`)).toBeDisabled()        // no batches while off
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/catalog-switched-off.png`, fullPage: true })
  await sw.click()
  await expect(sale).toContainText('On sale', { timeout: 20_000 })
  await expect(page.getByTestId(`add-${SKU}`)).toBeEnabled()
  const log = (await audit(page.request, 'cmd.personalization.set_product_active')).filter((e) => e.payload.sku === SKU)
  expect(log.map((e) => e.payload.active)).toEqual([true, false])   // newest first
})

test('a product in a stock order cannot be deleted: it is switched off instead', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await importProduct(page, KEPT, `Kept easel ${stamp}`)
  const key = `e2e${stamp.toLowerCase()}kept`
  const r = await send(page, 'catalog.batch', { items: [{ product: 'catalog', sku: KEPT, quantity: 1 }] }, key)
  expect(r.status(), await r.text()).toBe(202)
  batchOrder = `stk-${key.slice(0, 8)}`
  await expect.poll(async () => (await page.request.get(`/api/v1/orders/${batchOrder}`)).status(), { timeout: 30_000 }).toBe(200)

  await page.getByTestId(`sku-${KEPT}`).getByRole('button', { name: `Edit ${KEPT}` }).click()
  await page.getByTestId('delete-product').click()
  const confirm = page.getByRole('dialog').filter({ hasText: `${KEPT} cannot be deleted` })
  await expect(confirm).toContainText('1 order(s) still refer to it')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/catalog-delete-blocked.png`, fullPage: true })
  await confirm.getByRole('button', { name: 'Switch off' }).click()
  await page.getByTestId('product-dialog').getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByTestId(`sale-${KEPT}`)).toContainText('Switched off', { timeout: 20_000 })
  // the gateway refuses it too, whatever the browser does
  const direct = await send(page, 'catalog.delete', { sku: KEPT })
  expect(direct.status()).toBe(409)
  expect(await direct.text()).toContain('switch it off instead')
})

test('a product nothing refers to is deleted after the confirmation, with its picture', async ({ page }) => {
  await signIn(page)
  const image = await page.getByTestId(`sku-image-${SKU}`).getAttribute('src')
  await page.getByTestId(`sku-${SKU}`).getByRole('button', { name: `Edit ${SKU}` }).click()
  await page.getByTestId('delete-product').click()
  const confirm = page.getByRole('dialog').filter({ hasText: `Delete ${SKU}?` })
  await expect(confirm).toContainText('This cannot be undone')
  await confirm.getByRole('button', { name: 'Delete for good' }).click()
  await expect(page.getByTestId(`sku-${SKU}`)).toHaveCount(0, { timeout: 20_000 })
  expect((await page.request.get(`/api/v1/catalog/products/${SKU}`)).status()).toBe(404)
  expect((await page.request.get(image!)).status()).toBe(404)
  const log = (await audit(page.request, 'cmd.personalization.delete_product')).filter((e) => e.payload.sku === SKU)
  expect(log).toHaveLength(1)
})

test('in Hebrew the switch and Delete read in Hebrew, right to left', async ({ page }) => {
  await signIn(page)
  // the person's saved language, as the language switch saves it (put back to English at the end)
  const lang = async (l: 'en' | 'he') => {
    const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
    const r = await page.request.patch('/api/v1/auth/prefs', { headers: { 'X-CSRF-Token': csrf }, data: { lang: l } })
    expect(r.status()).toBe(200)
  }
  await lang('he')
  try {
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
    await expect(page.getByTestId(`sale-${KEPT}`)).toContainText('כבוי', { timeout: 20_000 })
    await expect(page.getByTestId(`active-${KEPT}`)).toHaveAttribute('aria-label', `הפעלת ${KEPT}: שוב במכירה`)
    await page.getByTestId(`sku-${KEPT}`).locator('.actions .btn').nth(1).click()        // the gear: Edit
    await expect(page.getByTestId('delete-product')).toHaveText('מחיקה')
    await expect(page.getByTestId('product-image-source')).toHaveText('מתמונת התצוגה של ה-3MF')
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/catalog-hebrew.png`, fullPage: true })
  } finally {
    await lang('en')
  }
})
