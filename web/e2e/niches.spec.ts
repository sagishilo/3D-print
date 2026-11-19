// End-to-end: niches on the running stack. The owner adds a niche with a QC checklist, imports a product into it
// (tagged with a second niche), focuses the dashboard on it from the top bar (Catalog, Orders and Finance follow and
// say they are filtered), moves the product to another niche in bulk, and archives the niche.
import { fileURLToPath } from 'node:url'

import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-5)
const NAME = `Pets ${stamp}`
const SLUG = `pets-${stamp}`
const SKU = `NICHE-${stamp.toUpperCase()}`

async function signIn(page: Page, path = '/live') {
  await page.goto(path)
  await page.locator('#user').fill('admin')
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function pickNiche(page: Page, slug: string) {
  await page.getByTestId('niche-switcher').click()
  await page.getByTestId(`niche-option-${slug}`).click()
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  await page.request.patch('/api/v1/auth/prefs', { headers: { 'X-CSRF-Token': csrf }, data: { niche: 'all' } })
  await page.close()
})

test('the owner adds a niche with its QC checklist', async ({ page }) => {
  await signIn(page, '/niches')
  await expect(page.getByTestId('niche-gifts')).toContainText('Text spelled exactly as ordered')     // seeded
  await page.getByTestId('add-niche').click()
  const dialog = page.getByTestId('niche-dialog')
  await dialog.getByTestId('niche-name').fill(NAME)
  await expect(dialog.getByTestId('niche-slug')).toHaveValue(SLUG)
  await dialog.getByTestId('niche-color-5').check({ force: true })
  await dialog.getByTestId('niche-margin').fill('55')
  await dialog.getByTestId('niche-checklist').fill('No sharp edges\nChew-safe material')
  await dialog.getByTestId('save-niche').click()
  const card = page.getByTestId(`niche-${SLUG}`)
  await expect(card).toContainText('Chew-safe material', { timeout: 20_000 })
  await expect(card).toContainText('55%')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m3-niches.png`, fullPage: true })
})

test('a product goes into the niche, tagged with a second one', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page, '/catalog')
  await page.getByTestId('import-3mf').click()
  const dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('file').setInputFiles(fileURLToPath(new URL('./fixtures/slab-easel.3mf', import.meta.url)))
  await expect(dialog.getByTestId('upload-info')).toContainText('sliced')
  await dialog.getByTestId('sku').fill(SKU)
  await dialog.getByTestId('name').fill(`Pet bowl stand ${stamp}`)
  await dialog.getByTestId('price').fill('49')
  await dialog.getByTestId('product-niche').selectOption(SLUG)
  await dialog.getByTestId('product-tag-kids').check()
  await dialog.getByTestId('save-product').click()
  await expect(page.getByTestId(`sku-niche-${SKU}`)).toContainText(NAME, { timeout: 20_000 })
})

test('the top bar focuses Catalog, Orders and Finance on one niche, and Show all brings everything back', async ({ page }) => {
  await signIn(page, '/catalog')
  await pickNiche(page, SLUG)
  await expect(page.getByTestId('niche-switcher')).toContainText(NAME)
  await expect(page.getByTestId('niche-filter-bar')).toContainText(`Filtered: ${NAME}`)
  await expect(page.getByTestId(`sku-${SKU}`)).toBeVisible()
  await expect(page.locator('[data-testid^="sku-niche-"]')).toHaveCount(1)            // only this niche's products
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/m3-catalog-filtered.png`, fullPage: true })

  // tagged: shown in the second niche too; not in a third
  await pickNiche(page, 'kids')
  await expect(page.getByTestId(`sku-${SKU}`)).toBeVisible()
  await pickNiche(page, 'mechanical')
  await expect(page.getByTestId(`sku-${SKU}`)).toHaveCount(0)

  // the choice is saved for this person: it is still there on another screen and after a reload
  await page.goto('/orders')
  await expect(page.getByTestId('niche-filter-bar')).toContainText('Mechanical parts')
  await page.reload()
  await expect(page.getByTestId('niche-switcher')).toContainText('Mechanical parts')
  await page.goto('/finance?tab=products')
  await expect(page.getByTestId('niche-margins')).toBeVisible()
  await expect(page.getByTestId('niche-filter-bar')).toBeVisible()
  await page.getByTestId('niche-show-all').click()
  await expect(page.getByTestId('niche-filter-bar')).toHaveCount(0)
  await expect(page.getByTestId('niche-switcher')).toContainText('All niches')

  // a link with ?niche= opens a screen in that niche
  await page.goto('/catalog?niche=gifts')
  await expect(page.getByTestId('niche-filter-bar')).toContainText('Personalised gifts')
  await pickNiche(page, 'all')
})

test('products move to another niche in bulk, and the niche is archived', async ({ page }) => {
  await signIn(page, '/catalog')
  await page.getByTestId('assign-niche').click()
  const dialog = page.getByTestId('assign-niche-dialog')
  await dialog.getByTestId('assign-target').selectOption('home')
  await dialog.getByTestId(`assign-${SKU}`).check()
  await dialog.getByTestId('assign-save').click()
  await expect(page.getByTestId(`sku-niche-${SKU}`)).toContainText('Home décor', { timeout: 20_000 })

  await page.goto('/niches')
  await page.getByTestId(`archive-niche-${SLUG}`).click()
  await page.locator('dialog.confirm').getByRole('button', { name: 'Archive' }).click()
  await expect(page.getByTestId(`niche-${SLUG}`)).toHaveCount(0, { timeout: 20_000 })
  await page.getByTestId('show-archived').check()
  await expect(page.getByTestId(`niche-${SLUG}`)).toContainText('archived')
  // an archived niche is not offered in the top bar
  await page.getByTestId('niche-switcher').click()
  await expect(page.getByTestId(`niche-option-${SLUG}`)).toHaveCount(0)
})
