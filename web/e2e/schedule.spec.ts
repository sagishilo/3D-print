// End-to-end: W2-C on the running stack. An order in a filament no printer has loaded (PLA green) reaches the queue
// and waits there: the Schedule shows a lane per printer, the job with its reason, the spool-change suggestion (also
// on Today); hold / move / pin change it on the fleet agent. A printer's Maintenance tab shows its counters and
// tasks; an owner adds a task, marks it done (logged) and removes it; plate swap switches on and off. The order is
// cancelled at the end.
import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')
test.describe.configure({ mode: 'serial' })

const stamp = Date.now().toString(36)
const key = `e2e${stamp}sched`
const orderId = `ord-${key.slice(0, 8)}`
let jobId = ''

async function signIn(page: Page, path = '/schedule') {
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

const state = async (page: Page) => {
  const r = await page.request.get(`/api/v1/orders/${orderId}`)
  return r.ok() ? ((await r.json()) as { state: string }).state : 'missing'
}

const queued = async (page: Page) => {
  const s = (await (await page.request.get('/api/v1/schedule')).json()) as { queue: { job_id: string; order_id: string }[] }
  return s.queue.filter((j) => j.order_id === orderId).map((j) => j.job_id)
}

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ ignoreHTTPSErrors: true })
  await signIn(page, '/today')
  if (!['missing', 'CANCELLED', 'SHIPPED'].includes(await state(page))) await send(page, 'orders.cancel', { order_id: orderId })
  await page.close()
})

test('a job no printer can take waits in the queue with its reason; hold, move and pin it', async ({ page }) => {
  test.setTimeout(150_000)
  await signIn(page)
  await expect(page.getByTestId('timeline')).toBeVisible()
  for (const p of ['bambu-01', 'bambu-02', 'voron-01']) await expect(page.getByTestId(`lane-${p}`)).toBeVisible()
  await expect(page.getByTestId('schedule-window')).toContainText('Overnight from')

  await send(page, 'orders.create', { customer_name: 'Schedule e2e', items: [{ product: 'nameplate', text: 'GREEN', color: 'green' }] }, key)
  // walk the order to Scheduling (each gate waits for a person by default)
  await expect.poll(async () => {
    const s = await state(page)
    if (s === 'LEAD' || s === 'RECEIVED') await send(page, 'orders.advance', { order_id: orderId, to_state: s === 'LEAD' ? 'RECEIVED' : 'PERSONALIZING' })
    if (s === 'PERSONALIZING') await send(page, 'orders.advance', { order_id: orderId, to_state: 'SCHEDULING' })
    return s
  }, { timeout: 90_000, intervals: [2_000] }).toBe('SCHEDULING')
  await expect.poll(() => queued(page), { timeout: 30_000 }).toHaveLength(1)
  jobId = (await queued(page))[0]

  await page.reload()
  await expect(page.getByTestId(`queue-row-${jobId}`)).toBeVisible()
  await expect(page.getByTestId(`reason-${jobId}`)).toHaveText('no printer has PLA green loaded')
  await expect(page.getByTestId('unplaced-PLA-green')).toContainText('PLA green: 1 job(s)')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/w2-c-schedule.png`, fullPage: true })

  await page.getByTestId(`hold-${jobId}`).click()
  await expect.poll(async () => { await page.reload(); return page.getByTestId(`reason-${jobId}`).innerText() },
    { timeout: 20_000, intervals: [1_500] }).toBe('held')
  await page.getByTestId(`hold-${jobId}`).click()                       // release
  await page.getByTestId(`top-${jobId}`).click()
  await expect.poll(async () => { await page.reload(); return page.getByTestId(`queue-row-${jobId}`).innerText() },
    { timeout: 20_000, intervals: [1_500] }).toContain('your order #1')
  await page.getByTestId(`auto-${jobId}`).click()
  await expect.poll(async () => { await page.reload(); return page.getByTestId(`queue-row-${jobId}`).innerText() },
    { timeout: 20_000, intervals: [1_500] }).not.toContain('your order')
  await expect(page.getByTestId(`hold-${jobId}`)).toHaveText('Hold')

  // as a table, and the same suggestion on Today
  await page.getByTestId('timeline-toggle').click()
  await expect(page.getByTestId('timeline-table')).toBeVisible()
  await page.goto('/today')
  await expect(page.getByTestId('today-item-spool:PLA:green')).toContainText('Load PLA green on a printer', { timeout: 20_000 })
})

test('the phone layout stacks the lanes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await expect(page.getByTestId('lane-bambu-01')).toBeVisible()
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(392)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/w2-c-schedule-phone.png`, fullPage: true })
})

test('a printer’s maintenance: counters, plate swap, a task added, done and removed', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page, '/fleet?printer=voron-01')
  await page.getByTestId('printer-drawer').getByRole('tab', { name: 'Maintenance' }).click()
  const panel = page.getByTestId('maintenance-panel')
  await expect(panel).toBeVisible()
  await expect(page.getByTestId('maintenance-counters')).toContainText('print hours')
  await expect(page.getByTestId('task-clean-plate')).toBeVisible()

  const label = `E2E check ${stamp}`
  const id = label.toLowerCase().replace(/[^a-z0-9]+/g, '-')
  await page.getByTestId('task-label').fill(label)
  await page.getByTestId('task-days').fill('30')
  await page.getByTestId('task-save').click()
  await expect(page.getByTestId(`task-${id}`)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId(`task-state-${id}`)).toContainText('OK')
  await page.getByTestId(`task-done-${id}`).click()
  await page.locator('dialog.confirm').getByRole('button', { name: 'Done' }).click()
  await expect(page.getByTestId('maintenance-log')).toContainText(label, { timeout: 20_000 })

  await page.getByTestId('auto-eject').check()
  await expect(page.getByTestId('plates-left')).toBeVisible({ timeout: 20_000 })
  if (SHOTS) await page.getByTestId('printer-drawer').screenshot({ path: `${SHOTS}/w2-c-maintenance.png` })
  await page.getByTestId('auto-eject').uncheck()
  await expect(page.getByTestId('plates-left')).toBeHidden({ timeout: 20_000 })

  await page.getByTestId(`task-archive-${id}`).click()
  await expect(page.getByTestId(`task-${id}`)).toBeHidden({ timeout: 20_000 })
})
