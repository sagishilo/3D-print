// End-to-end: Import from link on the running stack. A MakerWorld link is recognised and kept (never fetched); the
// MakerWorld-style 3MF fills title, designer and licence, and its personal-use licence makes it start switched off;
// switching it on asks for the owner's word that they hold the commercial rights (recorded); the card links to the
// model page. A Thingiverse link fills the form from the Thingiverse connector in fake mode (off again at the end).
import { fileURLToPath } from 'node:url'

import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-5).toUpperCase()
const SKU = `LINK-${stamp}`
const MW = 'https://makerworld.com/en/models/111937'

async function signIn(page: Page, path = '/catalog') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.locator('button[type=submit]').click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

async function send(page: Page, name: string, body: object) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''
  const r = await page.request.post(`/api/v1/commands/${name}`, { headers: { 'X-CSRF-Token': csrf }, data: body })
  expect(r.status(), await r.text()).toBe(202)
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  await send(page, 'integrations.configure', { connector_id: 'thingiverse', enabled: false })
  const exists = (await page.request.get(`/api/v1/catalog/products/${SKU}`)).ok()
  if (exists) await send(page, 'catalog.delete', { sku: SKU })            // nothing refers to it: it goes
  await page.close()
})

test('a MakerWorld link and its 3MF: details from the file, switched off for its licence', async ({ page }) => {
  await signIn(page)
  await page.getByTestId('import-link').click()
  const dialog = page.getByTestId('product-dialog')
  await expect(dialog.getByTestId('link-url')).toBeFocused()
  await dialog.getByTestId('link-url').fill('makerworld.com/en/models/111937-pokemon-card-stand?from=search')
  await dialog.getByTestId('link-check').click()
  await expect(dialog.getByTestId('link-info')).toContainText('MakerWorld model 111937')
  await expect(dialog.getByTestId('link-note')).toContainText('no public API')
  await expect(dialog.getByTestId('link-open')).toHaveAttribute('href', MW)
  await expect(dialog.getByTestId('link-url')).toHaveValue(MW)

  await dialog.getByTestId('file').setInputFiles(fileURLToPath(new URL('./fixtures/makerworld-easel.3mf', import.meta.url)))
  await expect(dialog.getByTestId('designer')).toHaveValue('E2E Designer')
  await expect(dialog.getByTestId('licence')).toHaveValue('Standard Digital File License')
  await expect(dialog.getByTestId('name')).toHaveValue('Pokemon slab easel')
  await expect(dialog.getByTestId('licence-verdict')).toContainText('Personal use only')
  await expect(dialog.getByTestId('brand-warning')).toContainText('pokemon')
  await expect(dialog.getByTestId('product-sale')).toContainText('Starts switched off')
  await dialog.getByTestId('sku').fill(SKU)
  await dialog.getByTestId('price').fill('29')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/catalog-import-link.png`, fullPage: true })
  await dialog.getByTestId('save-product').click()
  await expect(page.getByTestId(`sku-${SKU}`)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId(`sale-${SKU}`)).toContainText('Switched off')
  await expect(page.getByTestId(`licence-${SKU}`)).toHaveText('Personal use only: no selling')
  await expect(page.getByTestId(`source-${SKU}`)).toHaveAttribute('href', MW)
  await expect(page.getByTestId(`source-${SKU}`)).toContainText('E2E Designer')
  const p = await (await page.request.get(`/api/v1/catalog/products/${SKU}`)).json()
  expect(p).toMatchObject({ source_url: MW, source_site: 'makerworld', source_id: '111937', designer: 'E2E Designer',
    licence_status: 'non_commercial', active: false })
})

test('switching it on needs the owner’s word on the commercial rights, which is recorded', async ({ page }) => {
  await signIn(page)
  await page.getByTestId(`active-${SKU}`).click()
  const confirm = page.getByRole('dialog').filter({ hasText: `Switch ${SKU} on?` })
  await expect(confirm).toContainText('does not allow selling prints')
  const go = confirm.getByRole('button', { name: 'Switch on' })
  await expect(go).toBeDisabled()                                            // the tick is required
  await confirm.getByTestId('confirm-check').check()
  await go.click()
  await expect(page.getByTestId(`sale-${SKU}`)).toContainText('On sale', { timeout: 20_000 })
  await expect(page.getByTestId(`licence-${SKU}`)).toHaveText(`Commercial rights confirmed by ${USER}`)
  // the gateway passes no shortcut: without the confirmation the agent refuses
  await send(page, 'catalog.active', { sku: SKU, active: false })
  await expect(page.getByTestId(`sale-${SKU}`)).toContainText('Switched off', { timeout: 20_000 })
  await send(page, 'catalog.active', { sku: SKU, active: true })
  await expect(page.getByText(`${SKU} not switched`).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId(`sale-${SKU}`)).toContainText('Switched off')
})

test('a Thingiverse link fills the form from the connector (fake mode)', async ({ page }) => {
  await signIn(page)
  await send(page, 'integrations.configure', { connector_id: 'thingiverse', enabled: true, mode: 'fake' })
  await page.getByTestId('import-link').click()
  const dialog = page.getByTestId('product-dialog')
  await dialog.getByTestId('link-url').fill('https://www.thingiverse.com/thing:4321/files')
  await expect(async () => {
    await dialog.getByTestId('link-check').click()
    await expect(dialog.getByTestId('link-use')).toBeVisible({ timeout: 3000 })      // the agent picks up the switch
  }).toPass({ timeout: 30_000 })
  await expect(dialog.getByTestId('link-info')).toContainText('Fidget spinner (fake)')
  await expect(dialog.getByTestId('link-info')).toContainText('test data')
  await dialog.getByTestId('link-use').click()
  await expect(dialog.getByTestId('name')).toHaveValue('Fidget spinner (fake)')
  await expect(dialog.getByTestId('licence-verdict')).toContainText('Licence allows selling prints')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await send(page, 'integrations.configure', { connector_id: 'thingiverse', enabled: false })
})
