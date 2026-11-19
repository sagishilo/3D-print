// End-to-end: costing on the running stack. A product with finishing minutes shows its cost per piece in Finance >
// Products; raising the labour rate in Settings raises that cost and lowers the margin; the price calculator quotes
// the product (WhatsApp text) and turns the quote into an order with the quoted price.
import { fileURLToPath } from 'node:url'

import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-5)
const SKU = `PRC-${stamp.toUpperCase()}`
let labourBefore = ''

async function signIn(page: Page, path = '/live') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function setLabour(page: Page, value: string) {
  await page.goto('/finance?tab=settings')
  const field = page.getByTestId('set-labour')
  await expect(field).not.toHaveValue('')
  await field.fill(value)
  await page.getByTestId('finance-settings').getByRole('button', { name: 'Save settings' }).click()
  await expect(page.locator('.toast').filter({ hasText: 'Settings saved' })).toBeVisible()
}

const cost = (page: Page) => page.getByTestId(`ue-cost-${SKU}`).innerText()

test.afterAll(async ({ browser }) => {
  if (!labourBefore) return
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  await setLabour(page, labourBefore)
  await page.close()
})

test('the labour rate changes the cost and margin of a piece', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page, '/catalog')
  await page.getByTestId('import-3mf').click()
  const dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('file').setInputFiles(fileURLToPath(new URL('./fixtures/slab-easel.3mf', import.meta.url)))
  await expect(dialog.getByTestId('upload-info')).toContainText('sliced')
  await dialog.getByTestId('sku').fill(SKU)
  await dialog.getByTestId('name').fill(`Priced easel ${stamp}`)
  await dialog.getByTestId('price').fill('35')
  await dialog.getByTestId('post-minutes').fill('30')
  await dialog.getByTestId('save-product').click()
  await expect(dialog).toBeHidden()
  await expect(page.getByTestId(`sku-${SKU}`)).toBeVisible({ timeout: 20_000 })

  await page.goto('/finance?tab=settings')
  labourBefore = await page.getByTestId('set-labour').inputValue()
  await page.goto('/finance?tab=products')
  await expect(page.getByTestId(`ue-${SKU}`)).toBeVisible({ timeout: 20_000 })
  const before = await cost(page)
  const marginBefore = await page.getByTestId(`ue-margin-${SKU}`).innerText()

  await setLabour(page, String(Number(labourBefore || 40) + 60))      // 30 finishing minutes: +₪30 a piece
  await page.goto('/finance?tab=products')
  await expect.poll(() => cost(page), { timeout: 20_000 }).not.toBe(before)
  expect(await page.getByTestId(`ue-margin-${SKU}`).innerText()).not.toBe(marginBefore)
  await expect(page.getByTestId(`ue-rec-${SKU}`)).not.toHaveText('—')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m5-unit-economics.png`, fullPage: true })
})

test('the price calculator quotes the product and turns the quote into an order', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page, '/pricing')
  await page.getByTestId('q-sku').selectOption(SKU)
  await page.getByTestId('q-qty').fill('2')
  await page.getByTestId('q-shipping').fill('20')
  await expect(page.getByTestId('q-text')).toContainText('2 ×', { timeout: 20_000 })
  await expect(page.getByTestId('q-text')).toContainText('משלוח: ₪20')
  const unit = (await page.getByTestId('q-unit-price').innerText()).trim()
  const unitAgorot = Math.round(Number(unit.replace(/[^0-9.]/g, '')) * 100)
  expect(unitAgorot % 1000 === 900 || (unitAgorot < 2000 && unitAgorot % 100 === 90)).toBe(true)   // ends in 9
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m5-calculator.png`, fullPage: true })

  await page.getByTestId('q-customer').fill(`Quote customer ${stamp}`)
  await page.getByTestId('q-order').click()
  await expect(page).toHaveURL(/\/orders/)
  const order = page.getByTestId('new-order')
  await expect(order).toBeVisible()
  await expect(order.locator('input').first()).toHaveValue(`Quote customer ${stamp}`)
  await expect(order.locator('input[inputmode="decimal"]').first()).toHaveValue((unitAgorot / 100).toFixed(2))
  await order.getByRole('button', { name: /Create order|Save/ }).click()
  await expect(page.locator('.toast').filter({ hasText: 'Order created' })).toBeVisible({ timeout: 20_000 })
})
