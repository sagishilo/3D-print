// End-to-end: users and roles on the running stack. The owner adds an operator and a viewer (one-time passwords),
// each signs in in their own browser and must choose a password, sees only what the role allows, is refused by the
// gateway when trying more, shows up in the audit log, and is signed out at once when the owner disables them.
import { type Browser, expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36).slice(-6)
const OPERATOR = `op-${stamp}`
const VIEWER = `vw-${stamp}`
const OWN_PASSWORD = 'a long and private passphrase'

async function signIn(page: Page, username: string, password: string, path = '/live') {
  await page.goto(path)
  await page.locator('#user').fill(username)
  await page.locator('#pass').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/users-${name}.png`, fullPage: true })
}

async function addUser(page: Page, username: string, role: string): Promise<string> {
  await page.getByTestId('add-user').click()
  await page.getByTestId('new-username').fill(username)
  await page.getByTestId('new-display-name').fill(username.toUpperCase())
  await page.getByTestId('new-role').selectOption(role)
  await page.getByTestId('create-user').click()
  const value = page.getByTestId('temp-password-value')
  await expect(value).toBeVisible()
  const temp = (await value.textContent())!.trim()
  expect(temp).toMatch(/^\w{4}-\w{4}-\w{4}-\w{4}$/)
  await page.getByTestId('temp-password-done').click()
  await expect(page.getByTestId(`user-${username}`)).toContainText('Waiting for first sign-in')
  return temp
}

/** A fresh browser for another person, signed in with a temporary password and then their own. */
async function firstSignIn(browser: Browser, username: string, temp: string): Promise<Page> {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  await signIn(page, username, temp, '/orders')
  await expect(page.getByTestId('password-required')).toBeVisible()
  await page.getByTestId('pw-current').fill(temp)
  await page.getByTestId('pw-new').fill(OWN_PASSWORD)
  await page.getByTestId('pw-again').fill(OWN_PASSWORD)
  await page.getByTestId('pw-save').click()
  await expect(page).toHaveURL((url) => url.pathname === '/orders')   // not /login?next=/orders
  await expect(page.getByRole('heading', { name: 'Order Pipeline' })).toBeVisible()
  return page
}

const temps: Record<string, string> = {}

test('the owner adds an operator and a viewer, each with a one-time password', async ({ page }) => {
  await signIn(page, 'admin', PASSWORD)
  await page.getByRole('link', { name: 'Users' }).click()
  await expect(page).toHaveURL(/\/users$/)
  await expect(page.getByTestId('user-admin')).toContainText('you')
  temps[OPERATOR] = await addUser(page, OPERATOR, 'operator')
  temps[VIEWER] = await addUser(page, VIEWER, 'viewer')
  await shot(page, '1-list')
})

test('an operator works the floor but cannot manage users or settings', async ({ browser }) => {
  const page = await firstSignIn(browser, OPERATOR, temps[OPERATOR])
  const nav = page.getByRole('navigation', { name: 'Main' })
  await expect(nav.getByRole('link', { name: 'Finance', exact: true })).toBeVisible()
  await expect(nav.getByRole('link', { name: 'Users', exact: true })).toHaveCount(0)
  await expect(page.getByTestId('new-order-button')).toBeEnabled()
  await expect(page.getByTestId('pause-all')).toBeEnabled()
  // owner-only: system toggles are disabled, there is no Audit tab, and /users sends you back
  await page.goto('/system?tab=config')
  await expect(page.getByTestId('settings').getByRole('switch').first()).toBeDisabled()
  await expect(page.getByRole('tab', { name: 'Audit log' })).toHaveCount(0)
  await page.goto('/users')
  await expect(page).toHaveURL(/\/live$/)
  // and the gateway refuses the owner-only command even when asked directly
  const csrf = (await page.context().cookies()).find((c) => c.name === 'pf_csrf')!.value
  const r = await page.request.post('/api/v1/commands/config.set', {
    headers: { 'X-CSRF-Token': csrf }, data: { key: 'orders.auto_personalize', value: true } })
  expect(r.status()).toBe(403)
  await shot(page, '2-operator')
  await page.context().close()
})

test('a viewer reads, changes nothing, and is signed out when disabled', async ({ browser, page }) => {
  const viewer = await firstSignIn(browser, VIEWER, temps[VIEWER])
  await expect(viewer.getByTestId('read-only')).toBeVisible()
  await expect(viewer.getByTestId('new-order-button')).toBeDisabled()
  await expect(viewer.getByTestId('pause-all')).toBeDisabled()
  await expect(viewer.getByTestId('pause-all')).toHaveAttribute('title', 'Needs the operator role')
  const nav = viewer.getByRole('navigation', { name: 'Main' })
  for (const hidden of ['Finance', 'System & Agents', 'Users']) await expect(nav.getByRole('link', { name: hidden, exact: true })).toHaveCount(0)
  await viewer.goto('/finance')
  await expect(viewer).toHaveURL(/\/live$/)
  const csrf = (await viewer.context().cookies()).find((c) => c.name === 'pf_csrf')!.value
  const r = await viewer.request.post('/api/v1/commands/printers.pause_all', { headers: { 'X-CSRF-Token': csrf }, data: { reason: 'x' } })
  expect(r.status()).toBe(403)
  await shot(viewer, '3-viewer')

  // the owner disables the viewer: their next request is refused and they land on the sign-in page
  await signIn(page, 'admin', PASSWORD, '/users')
  await page.getByTestId(`toggle-${VIEWER}`).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Disable' }).click()
  await expect(page.getByTestId(`user-${VIEWER}`)).toContainText('Disabled')
  await viewer.reload()
  await expect(viewer.locator('#pass')).toBeVisible()
  await viewer.context().close()
})

test('the audit log shows who did what, filtered by person and action', async ({ page }) => {
  await signIn(page, 'admin', PASSWORD, '/system?tab=audit')
  const audit = page.getByTestId('audit')
  await expect(audit).toBeVisible()
  await page.getByTestId('audit-actor').selectOption(OPERATOR)
  await expect(audit.locator('tbody')).toContainText('Sign in')
  await expect(audit.locator('tbody')).toContainText('Password change')
  await page.getByTestId('audit-actor').selectOption('admin')
  await page.getByTestId('audit-action').selectOption('users')
  const rows = audit.locator('tbody tr.selectable')
  await expect(rows.filter({ hasText: 'Add user' }).first()).toBeVisible()
  await expect(rows.filter({ hasText: `user: ${VIEWER}` }).filter({ hasText: 'Change user' })).toHaveCount(1)
  await rows.filter({ hasText: `user: ${OPERATOR}` }).first().click()
  await expect(audit.locator('pre')).toContainText('"role": "operator"')
  await shot(page, '4-audit')
})

test('cleanup: the operator is disabled too', async ({ page }) => {
  await signIn(page, 'admin', PASSWORD, '/users')
  await page.getByTestId(`toggle-${OPERATOR}`).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Disable' }).click()
  await expect(page.getByTestId(`user-${OPERATOR}`)).toContainText('Disabled')
})
