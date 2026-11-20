// End-to-end: W2-D on the running stack. Telegram (test mode) gets a test message and the outbox says where it went;
// the phone app's manifest, service worker and the farm's CA certificate are served; the dashboard switches to Hebrew
// (right to left, the older screens and the server's text too), screen by screen with screenshots, fits a phone,
// and switches back. The person's language and the channel are reset at the end (other tests read English).
import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

async function signIn(page: Page, path = '/today') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.locator('form button[type="submit"]').click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

const csrf = async (page: Page) => (await page.context().cookies()).find((c) => c.name === 'pf_csrf')?.value ?? ''

async function send(page: Page, name: string, body: object) {
  const r = await page.request.post(`/api/v1/commands/${name}`, { headers: { 'X-CSRF-Token': await csrf(page) }, data: body })
  expect(r.status(), `${name}: ${await r.text()}`).toBe(202)
}

async function setLang(page: Page, lang: 'en' | 'he') {
  const r = await page.request.patch('/api/v1/auth/prefs', { headers: { 'X-CSRF-Token': await csrf(page) }, data: { lang } })
  expect(r.ok()).toBe(true)
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page)
  await setLang(page, 'en')
  await send(page, 'integrations.configure', { connector_id: 'telegram', enabled: false })
  await page.close()
})

test('a Telegram test message, and where each notification went', async ({ page }) => {
  test.setTimeout(60_000)
  await signIn(page, '/integrations')
  await send(page, 'integrations.configure', { connector_id: 'telegram', enabled: true, mode: 'fake', settings: { chat_ids: '123456' } })
  await expect(page.getByTestId('test-alert-telegram')).toBeEnabled({ timeout: 20_000 })
  await page.getByTestId('test-alert-telegram').click()
  await expect.poll(async () => {
    const r = (await (await page.request.get('/api/v1/notifications')).json()) as { notifications: { topic: string; channels: string[]; status: string }[] }
    return r.notifications.find((n) => n.topic === 'test')?.channels ?? []
  }, { timeout: 30_000 }).toContain('telegram (test)')
  await page.reload()
  await expect(page.getByTestId('outbox')).toContainText('sent to telegram (test)')
})

test('the phone app: manifest, service worker and the farm’s certificate', async ({ page }) => {
  await signIn(page)
  const manifest = await page.request.get('/manifest.webmanifest')
  expect(manifest.ok()).toBe(true)
  const m = (await manifest.json()) as { name: string; start_url: string; icons: { src: string }[] }
  expect(m.name).toBe('PrintFarm Ops')
  expect(m.start_url).toBe('./today')
  for (const icon of m.icons) expect((await page.request.get(`/${icon.src}`)).ok()).toBe(true)
  const sw = await page.request.get('/sw.js')
  expect(sw.ok()).toBe(true)
  expect(sw.headers()['cache-control']).toBe('no-cache')
  expect(await sw.text()).toContain("startsWith('/api/')")                    // the API is never kept
  const ca = await page.request.get('/ca.crt')
  expect(ca.ok()).toBe(true)
  expect(await ca.text()).toContain('-----BEGIN CERTIFICATE-----')
  expect(await page.locator('link[rel="manifest"]').getAttribute('href')).toBe('/manifest.webmanifest')
  await page.goto('/system')
  await expect(page.getByTestId('phone-address')).toHaveText(new URL(page.url()).origin)
})

const SCREENS: [string, string, RegExp][] = [
  ['/today', 'today', /היום/],
  ['/orders', 'orders', /מסלול ההזמנות/],
  ['/schedule', 'schedule', /לוח הדפסות/],
  ['/fleet', 'fleet', /צי המדפסות/],
  ['/store', 'store', /הזמנות מהחנות/],
  ['/finance?tab=documents', 'finance', /כספים/],
  ['/catalog', 'catalog', /קטלוג/],
  ['/filament', 'inventory', /מלאי/],
  ['/system', 'system', /מערכת וסוכנים/],
  ['/integrations', 'integrations', /חיבורים/],
]

test('the dashboard in Hebrew, right to left, screen by screen', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.getByTestId('user-menu').click()
  await Promise.all([page.waitForEvent('load'), page.getByTestId('lang-he').click()])
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveAttribute('lang', 'he')
  await expect(page.locator('nav')).toContainText('מסלול ההזמנות')
  for (const [path, name, heading] of SCREENS) {
    await page.goto(path)
    await expect(page.locator('h1').first()).toContainText(heading, { timeout: 20_000 })
    // the side navigation sits on the right in RTL
    const nav = await page.locator('nav').first().boundingBox()
    expect(nav!.x).toBeGreaterThan(600)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/w2-d-rtl-${name}.png`, fullPage: true })
  }
  // the server's text in Hebrew too (Today's tasks, the schedule's reasons)
  await page.goto('/today')
  await expect(page.locator('main')).not.toContainText('wait for you', { timeout: 10_000 })
})

test('Hebrew fits a phone, and the language switches back', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')            // the person's language followed them
  for (const path of ['/today', '/orders', '/store']) {
    await page.goto(path)
    await expect(page.locator('h1').first()).toBeVisible()
    const wide = await page.evaluate(() => [...document.querySelectorAll('body *')]
      .filter((e) => e.getBoundingClientRect().right > 392 || e.getBoundingClientRect().left < -2)
      .slice(-4).map((e) => `${e.tagName}.${(e as HTMLElement).className} ${Math.round(e.getBoundingClientRect().left)}..${Math.round(e.getBoundingClientRect().right)}`))
    expect(await page.evaluate(() => document.documentElement.scrollWidth), `${path}: ${wide.join(' | ')}`).toBeLessThanOrEqual(392)
  }
  if (SHOTS) {
    await page.goto('/today')
    await page.screenshot({ path: `${SHOTS}/w2-d-rtl-phone.png`, fullPage: true })
  }
  await page.locator('[data-testid="user-menu"]').click()
  await Promise.all([page.waitForEvent('load'), page.getByTestId('lang-en').click()])
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr')
  await expect(page.locator('nav')).toContainText('Order Pipeline')
})
