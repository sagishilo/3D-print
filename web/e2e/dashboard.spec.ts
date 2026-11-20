// End-to-end: the dashboard in a real browser against the simulator stack (see playwright.config.ts).
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const USER = process.env.PRINTFARM_USER ?? 'admin'          // an owner of the running stack
const PROJECT = process.env.PRINTFARM_COMPOSE_PROJECT ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

async function signIn(page: Page, path = '/live') {
  await page.goto(path)
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

/** One dead letter holding a valid set_config command, as an agent would leave it after its last retry. */
function injectDeadLetter(changedBy: string): void {
  const script = `
import asyncio, os
from printfarm.bus.nats_bus import NatsBus
from printfarm.contracts import new_message
from printfarm.contracts.messages import DeadLettered, SetConfig
async def main():
    bus = await NatsBus.connect(os.environ["NATS_URL"], name="e2e-dlq")
    cmd = new_message(SetConfig(key="inventory.auto_reorder", value=True, changed_by="${changedBy}"), source="/e2e")
    await bus.publish(new_message(DeadLettered(original_subject=cmd.nats_subject(), original_id=cmd.id,
        consumer="coordinator__cmd_coordinator_set_config", error="RuntimeError('database briefly unavailable')",
        deliveries=5, original=cmd.model_dump(mode="json")), source="/consumers/coordinator", cause=cmd))
    await bus.nc.drain()
asyncio.run(main())
`
  execFileSync('docker', ['compose', '-p', PROJECT, 'exec', '-T', 'coordinator', 'python', '-c', script], { stdio: 'pipe' })
}

test('sign-in sends you back to the screen you asked for', async ({ page }) => {
  await page.goto('/system?tab=agents')
  await expect(page).toHaveURL(/\/login\?next=/)
  await shot(page, '0-sign-in')
  await page.locator('#pass').fill('not-the-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText('Wrong username or password')
  await shot(page, '0-sign-in-error')
  await page.locator('#user').fill(USER)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/system\?tab=agents$/)
  await expect(page.getByRole('tab', { name: 'Agents', selected: true })).toBeVisible()
})

test('live floor: cameras, state, telemetry replay and event replay', async ({ page }) => {
  await signIn(page)
  await expect(page.getByRole('heading', { name: 'Live floor' })).toBeVisible()
  await expect(page.getByTestId('live-indicator')).toContainText('Live ·')
  for (const id of ['bambu-01', 'bambu-02', 'voron-01']) {
    const tile = page.getByTestId(`printer-${id}`)
    await expect(tile).toBeVisible()
    await expect(tile.getByRole('img', { name: `${id} camera: LIVE` })).toBeVisible({ timeout: 30_000 })
  }
  await expect(page.getByTestId('printer-voron-01')).toContainText('Klipper')
  await shot(page, '1-live-floor')

  await page.getByTestId('printer-bambu-01').getByRole('button', { name: 'History' }).click()
  await expect(page).toHaveURL(/printer=bambu-01/)
  const replay = page.locator('section.replay')
  await expect(replay.getByRole('heading', { name: 'Replay · bambu-01' })).toBeVisible()
  await expect(replay.getByRole('img', { name: /bambu-01 temperatures and progress/ })).toBeVisible()
  const scrub = replay.locator('#replay-scrub')
  await scrub.focus()
  await page.keyboard.press('Home')
  await expect(replay.locator('.readout .chip.accent')).toHaveCount(0) // moved off "live"
  await page.keyboard.press('End')
  await expect(replay.locator('.readout')).toContainText('live')

  await page.getByTestId('replay').click()
  await expect(page.getByRole('status').filter({ hasText: /Replayed \d+ event/ })).toBeVisible()
  await shot(page, '2-live-floor-replay')
})

test('system & agents: health, NATS, servers and printers', async ({ page }) => {
  await signIn(page, '/system')
  await expect(page.getByRole('heading', { name: 'System & Agents' })).toBeVisible()
  for (const agent of ['coordinator', 'telemetry', 'fleet', 'personalization', 'inventory', 'finance', 'social', 'trend', 'integrations']) {
    await expect(page.getByTestId(`agent-${agent}`)).toHaveAttribute('data-status', 'healthy', { timeout: 40_000 })
  }
  for (const server of ['gateway', 'nats', 'postgres', 'go2rtc']) {
    await expect(page.getByTestId(`server-${server}`)).toContainText('OK')
  }
  await expect(page.getByTestId('system-printer-voron-01')).toContainText('Online')
  await shot(page, '3-system-overview')

  await page.getByRole('tab', { name: 'NATS' }).click()
  await expect(page.getByTestId('nats-connections')).toContainText('gateway')
  await expect(page.getByTestId('nats-connections')).toContainText('agent:telemetry')
  await expect(page.getByTestId('nats-streams')).toContainText('TELEMETRY')
  await shot(page, '4-system-nats')
})

test('automation setting is changed through an audited command', async ({ page }) => {
  await signIn(page, '/system?tab=config')
  const reorder = page.getByRole('switch', { name: 'Auto-reorder filament' })
  await expect(reorder).toHaveAttribute('aria-checked', 'true')
  await reorder.click()
  await expect(reorder).toHaveAttribute('aria-checked', 'false')
  await expect(page.getByText(`Last change: Auto-reorder filament disabled by ${USER}`)).toBeVisible()
  await shot(page, '5-system-config')
  await reorder.click()
  await expect(page.getByText(`Last change: Auto-reorder filament enabled by ${USER}`)).toBeVisible()

  // switching off the safety auto-pause asks first; cancelling changes nothing
  const autoPause = page.getByRole('switch', { name: 'Failure detection auto-pause' })
  await autoPause.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Turn off auto-pause?')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(autoPause).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('switch', { name: 'Quiet hours 23:00–07:00' })).toBeDisabled()
})

test('a dead letter is inspected and replayed', async ({ page }) => {
  test.skip(!PROJECT, 'set PRINTFARM_COMPOSE_PROJECT to inject a dead letter')
  const marker = `e2e-${Date.now()}`
  injectDeadLetter(marker)
  await signIn(page, '/system?tab=dlq')
  const row = page.locator('tr', { hasText: 'cmd.coordinator.set_config' }).first()
  await expect(row).toBeVisible()
  await row.click()
  const inspector = page.getByTestId('dlq-inspector')
  await expect(inspector).toContainText(marker)
  await expect(inspector).toContainText('database briefly unavailable')
  await shot(page, '6-system-dlq')
  await page.getByTestId('dlq-replay').click()
  await page.getByRole('dialog').getByRole('button', { name: 'Replay' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Message replayed' })).toBeVisible()
  await expect(page.getByText('The dead letter queue is empty')).toBeVisible()
  // the replayed command reached the coordinator: the setting now names the replay as its last change
  await page.getByRole('tab', { name: 'Config' }).click()
  await expect(page.getByText(`by ${marker}`)).toBeVisible()
})

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the live floor fits a phone and the menu opens the navigation', async ({ page }) => {
    await signIn(page)
    await expect(page.getByTestId('printer-bambu-01')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
    await shot(page, '7-phone-live')
    const nav = page.getByRole('navigation', { name: 'Main' })
    await expect(nav).not.toBeInViewport()
    await page.getByRole('button', { name: 'Open navigation' }).click()
    await expect(nav).toBeInViewport()
    await nav.getByRole('link', { name: /System & Agents/ }).click()
    await expect(page.getByRole('heading', { name: 'System & Agents' })).toBeVisible()
    await expect(nav).not.toBeInViewport() // the drawer closes after navigating
    await expect(page.getByTestId('agent-telemetry')).toBeVisible()
    const systemOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(systemOverflow).toBeLessThanOrEqual(0) // wide tables scroll inside their panel, not the page
    await shot(page, '8-phone-system')
  })
})

test.describe('printer fleet', () => {
  test('details: hardware readings and a calibration run from start to finish', async ({ page }) => {
    await signIn(page, '/fleet')
    await expect(page.getByRole('heading', { name: 'Printer Fleet' })).toBeVisible()
    await expect(page.getByTestId('printer-voron-01')).toBeVisible()
    await shot(page, '10-fleet')

    await page.getByRole('button', { name: 'Details of bambu-02' }).click()
    const drawer = page.getByTestId('printer-drawer')
    await expect(drawer).toBeVisible()
    await drawer.getByRole('tab', { name: 'Hardware' }).click()
    await expect(drawer.getByTestId('hw-connection')).toContainText('01.08.05.00')
    await expect(drawer.getByTestId('hw-ams')).toContainText('PETG')
    await shot(page, '11-fleet-hardware')
    await drawer.getByRole('button', { name: 'Close details' }).click()

    await page.getByRole('button', { name: 'Details of voron-01' }).click()
    await drawer.getByRole('tab', { name: 'Calibration' }).click()
    await drawer.getByTestId('routine-home').getByRole('button', { name: 'Run' }).click()
    const dialog = page.getByRole('dialog', { name: /Home all axes on voron-01/ })
    const start = dialog.getByRole('button', { name: 'Start home all axes' })
    await expect(start).toBeDisabled()
    await dialog.getByTestId('confirm-check').check()
    await start.click()
    // this run must be seen running, then finished: only then is the newest row this run's result
    const banner = drawer.getByRole('status').filter({ hasText: 'Home all axes running' })
    await expect(banner).toBeVisible()
    await expect(banner).toBeHidden({ timeout: 30_000 })
    const newest = drawer.locator('table.runs tbody tr').first()
    await expect(newest).toContainText('Home all axes')
    await expect(newest).toContainText('Done')
    await shot(page, '12-fleet-calibration')
    await drawer.getByRole('tab', { name: 'History' }).click()
    await expect(drawer.getByRole('heading', { name: 'Verified pauses' })).toBeVisible()
  })

  test('batch actions apply to the printers in a fitting state', async ({ page }) => {
    test.setTimeout(300_000)
    await signIn(page, '/fleet')
    // The checks below need an idle farm: let plates queued by other tests (catalog batches) finish first.
    await expect(page.getByRole('group', { name: 'Filter by state' })).not.toContainText(/printing|heating|paused/, { timeout: 240_000 })
    await page.getByRole('button', { name: 'Select all' }).click()
    const bar = page.getByTestId('batch-bar')
    await expect(bar).toContainText('3 selected')
    await expect(bar.getByTestId('batch-pause')).toBeDisabled() // nothing is printing
    await expect(bar.getByTestId('batch-home')).toContainText('3')
    await shot(page, '13-fleet-batch')
    await bar.getByTestId('batch-home').click()
    const dialog = page.getByRole('dialog', { name: /Home axes: 3 printers/ })
    await expect(dialog).toContainText('bambu-01, bambu-02, voron-01')
    await dialog.getByTestId('confirm-check').check()
    await dialog.getByRole('button', { name: 'Home axes' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Home axes: sent to 3 printers' })).toBeVisible()
    await expect(bar).toBeHidden()
    // each printer reports its own result in the log
    const log = page.getByTestId('fleet-log')
    for (const id of ['bambu-01', 'bambu-02', 'voron-01']) {
      await expect(log.locator('tr', { hasText: `${id}: home done` }).first()).toBeVisible({ timeout: 30_000 })
    }
    await page.locator('#log-severity').selectOption('warning')
    await expect(log.locator('tbody tr').filter({ hasText: 'Info' })).toHaveCount(0)
    await shot(page, '14-fleet-log')
  })
})

// Drop just under a column's header: columns grow as tall as the longest one, so their centre can be off screen and
// a drag that has to scroll the page loses its HTML5 drop.
const DROP_AT_HEADER = { targetPosition: { x: 40, y: 24 } }

test.describe('order pipeline', () => {
  test('a two-line order goes from intake over the board to shipped and paid', async ({ page }) => {
    test.setTimeout(300_000)
    const customer = `E2E ${Date.now()}`
    await signIn(page, '/orders')
    await expect(page.getByRole('heading', { name: 'Order Pipeline' })).toBeVisible()

    await page.getByTestId('new-order-button').click()
    const dialog = page.getByTestId('new-order')
    await dialog.getByTestId('customer-name').fill(customer)
    await dialog.getByTestId('line-1').locator('input').first().fill('Noa')
    await dialog.getByRole('button', { name: 'Add nameplate' }).click()
    await dialog.getByTestId('line-2').locator('input').first().fill('Dan')
    await expect(dialog.getByTestId('order-total')).toContainText('₪90.00') // 2 × ₪45 default price
    await shot(page, '20-orders-new')
    await dialog.getByRole('button', { name: 'Create order' }).click()

    // the switches are off by default: the order waits in "Order received"
    const card = (stage: string) => page.getByTestId(`column-${stage}`).locator('article', { hasText: customer })
    await expect(card('RECEIVED')).toBeVisible()

    // a drop the pipeline does not allow is refused, with the reason
    await card('RECEIVED').dragTo(page.getByTestId('column-PRINTING'), DROP_AT_HEADER)
    await expect(page.getByRole('alert').filter({ hasText: 'printing starts when a printer takes the plate' })).toBeVisible()

    // dropping into Personalization dispatches one nameplate job per line
    await card('RECEIVED').dragTo(page.getByTestId('column-PERSONALIZING'), DROP_AT_HEADER)
    await expect(card('PERSONALIZING')).toContainText('drag to scheduling', { timeout: 90_000 })
    await expect(page.getByTestId('nameplates')).toContainText('“Dan”')
    await shot(page, '21-orders-board')

    // dropping into Slicing / scheduling sends the units to the fleet; printers take it from there
    await card('PERSONALIZING').dragTo(page.getByTestId('column-SCHEDULING'), DROP_AT_HEADER)
    await expect(card('QC')).toBeVisible({ timeout: 180_000 })

    await card('QC').click()
    const drawer = page.getByTestId('order-drawer')
    const itemRows = drawer.locator('table').first().locator('tbody tr')
    await expect(itemRows).toHaveCount(2)
    await expect(itemRows.nth(1)).toBeInViewport() // not clipped by the scrolling panel
    await expect(itemRows.nth(1)).toContainText('Dan')
    // nameplates are personalised gifts: that niche's QC checklist is ticked before passing, and kept with the check
    const checks = drawer.getByTestId('qc-checklist').locator('input[type=checkbox]')
    await expect(checks).toHaveCount(3)
    for (const c of await checks.all()) await c.check()
    await drawer.getByTestId('qc-pass').click()
    await expect(drawer.getByTestId('qc-checklist-result')).toContainText('checklist 3/3', { timeout: 30_000 })
    await drawer.getByLabel('Carrier').fill('Israel Post')
    await drawer.getByTestId('ship').click()
    await expect(card('SHIPPED')).toBeVisible({ timeout: 30_000 })

    await drawer.getByTestId('pay-amount').fill('90')
    await drawer.getByRole('button', { name: 'Record payment' }).click()
    await expect(drawer.locator('.sum')).toContainText(/Balance\s*₪0\.00/, { timeout: 30_000 })
    await expect(drawer.locator('.history')).toContainText('Shipped')
    await shot(page, '22-orders-drawer')
  })
})

test.describe('filament', () => {
  test('scan-register a spool, a minimum that raises and clears an alert, weigh a spool', async ({ page }) => {
    const label = `QR-${Date.now()}`
    await signIn(page, '/filament')
    await expect(page.getByRole('heading', { name: 'Inventory', exact: true })).toBeVisible()
    const rules = page.getByTestId('reorder-rules')
    await expect(rules.getByTestId('rule-PLA-white')).toBeVisible()
    await shot(page, '30-filament')

    // an unknown label opens the registration with the label as the spool id
    await page.getByTestId('scan').fill(label)
    await page.getByRole('button', { name: 'Find' }).click()
    const dialog = page.getByTestId('spool-dialog')
    await dialog.getByTestId('reg-color').fill('navy')
    await dialog.getByRole('button', { name: 'Register spool' }).click()
    await expect(page.getByTestId(`spool-${label}`)).toContainText('PLA · navy')

    // a minimum far above the stock raises a "below minimum" alert; putting it back clears it on its own
    const white = rules.getByTestId('rule-PLA-white')
    await white.getByRole('button', { name: 'Edit' }).click()
    await white.getByTestId('rule-min').fill('99999')
    await white.getByRole('button', { name: 'Save' }).click()
    const alerts = page.getByTestId('inventory-alerts')
    await expect(alerts).toContainText('PLA white: ', { timeout: 20_000 })
    await shot(page, '31-filament-alert')
    await white.getByRole('button', { name: 'Edit' }).click()
    await white.getByTestId('rule-min').fill('1000')
    await white.getByRole('button', { name: 'Save' }).click()
    await expect(alerts.getByText(/PLA white: .* below the minimum of 99999 g/)).toBeHidden({ timeout: 20_000 })

    // weigh the new spool
    await page.getByTestId(`spool-${label}`).click()
    await dialog.getByTestId('weigh-grams').fill('640')
    await dialog.getByRole('button', { name: 'Save weight' }).click()
    await expect(page.getByTestId(`spool-${label}`)).toContainText('640 g', { timeout: 20_000 })
  })
})

test.describe('finance', () => {
  test('payment and expense are booked with VAT, settings confirmed, ledger exported', async ({ page }) => {
    const receipt = `RC-${Date.now()}`
    await signIn(page, '/finance')
    await expect(page.getByRole('heading', { name: 'Finance & Accounting' })).toBeVisible()

    // income without an order (a market stall): 708.00 gross = 600.00 net + 108.00 VAT at 18 %
    await page.getByTestId('record-payment').click()
    const pay = page.getByTestId('payment-dialog')
    await pay.getByTestId('money-amount').fill('708')
    await pay.getByLabel('Method').selectOption('cash')
    await pay.getByLabel('Receipt no.').fill(receipt)
    await pay.getByRole('button', { name: 'Record payment' }).click()
    await page.getByRole('tab', { name: 'Income' }).click()
    const row = page.locator('tr', { hasText: receipt })
    await expect(row).toContainText('₪708.00', { timeout: 20_000 })
    await expect(row).toContainText('₪108.00')
    await expect(row).toContainText('₪600.00')

    // an expense with a tax invoice: its VAT is reclaimed (input VAT)
    await page.getByTestId('add-expense').click()
    const exp = page.getByTestId('expense-dialog')
    await exp.getByTestId('expense-category').selectOption('shipping')
    await exp.getByTestId('money-amount').fill('236')
    await exp.getByLabel('Vendor').fill('Israel Post')
    await exp.getByRole('button', { name: 'Add expense' }).click()
    await page.getByRole('tab', { name: 'Expenses' }).click()
    await expect(page.locator('tr', { hasText: 'Israel Post' }).first()).toContainText('₪200.00', { timeout: 20_000 })

    await page.getByRole('tab', { name: 'Overview' }).click()
    await expect(page.getByTestId('expense-mix')).toContainText('Shipping')
    await shot(page, '40-finance')
    await page.getByRole('tab', { name: 'VAT' }).click()
    await expect(page.getByTestId('vat')).toContainText('Nov–Dec')
    await shot(page, '41-finance-vat')

    // the CSV the accountant gets
    const csv = await page.request.get('/api/v1/finance/export.csv?ledger=income')
    expect(await csv.text()).toContain(`708.00,108.00,600.00,${receipt}`)

    // confirming the settings removes the placeholder warning
    await page.getByRole('tab', { name: 'Settings' }).click()
    await page.getByTestId('set-confirm').check()
    await page.getByRole('button', { name: 'Save settings' }).click()
    await expect(page.getByTestId('placeholder-banner')).toBeHidden({ timeout: 20_000 })
    await expect(page.getByTestId('finance-settings')).toContainText(`confirmed by ${USER}`)
  })
})

test.describe('instant catalog', () => {
  test('import a sliced 3MF, build a batch with its estimate, queue it as a stock order', async ({ page }) => {
    test.setTimeout(180_000)
    const sku = `E2E-${Date.now().toString(36).toUpperCase()}`
    await signIn(page, '/catalog')
    await expect(page.getByRole('heading', { name: 'High-Demand Instant-Print Catalog' })).toBeVisible()

    // the gateway reads the file and says what it holds; the personalization agent keeps it
    await page.getByTestId('import-3mf').click()
    const dialog = page.getByTestId('product-dialog')
    await dialog.getByTestId('file').setInputFiles(fileURLToPath(new URL('./fixtures/slab-easel.3mf', import.meta.url)))
    await expect(dialog.getByTestId('upload-info')).toContainText('sliced (1 plate)')
    await expect(dialog.getByTestId('upload-info')).toContainText('slicer: 1 h 30 m · 31.3 g')
    await dialog.getByTestId('sku').fill(sku)
    await dialog.getByTestId('name').fill('E2E slab easel')
    await dialog.getByTestId('price').fill('35')
    await shot(page, '50-catalog-import')
    await dialog.getByTestId('save-product').click()

    const card = page.getByTestId(`sku-${sku}`)
    await expect(card).toContainText('3MF · sliced', { timeout: 20_000 })
    await expect(card).toContainText('1 h 30 m')

    // into the batch: the bar shows plates, time and filament as the scheduler would pack them
    await card.getByTestId(`add-${sku}`).click()
    // Wait for the batch to take the suggested quantity first, or it can overwrite the 2 typed below.
    await expect(page.getByTestId('batch-bar')).toContainText(`${sku} ×`)
    await card.getByTestId(`qty-${sku}`).fill('2')
    await card.getByTestId(`qty-${sku}`).blur()
    const bar = page.getByTestId('batch-bar')
    await expect(bar).toContainText(`${sku} × 2`)
    await expect(bar.getByTestId('batch-estimate')).toContainText('2 plates · 3 h 00 m · 63 g')   // sliced: one per plate
    await shot(page, '51-catalog-batch')
    await bar.getByTestId('queue-batch').click()
    await page.locator('dialog.confirm').getByRole('button', { name: 'Queue batch' }).click()
    await expect(bar).toBeHidden()

    // the stock order skips the gates: on the pipeline it is already past Personalization
    await page.goto('/orders')
    const stock = page.locator('article', { hasText: 'Stock batch' }).filter({ hasText: /stk-/ }).first()
    await expect(stock).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('column-RECEIVED').locator('article', { hasText: 'Stock batch' })).toHaveCount(0)
  })
})

test.describe('social listening', () => {
  // The stack runs the social agent in test mode (SOCIAL_LLM=fake: keyword triage, canned drafts, no Claude calls)
  // with the real local embedding model, so the prefilter is the production one.
  test('a pasted post becomes a lead; the reply is edited, approved & copied, and marked posted', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    const n = Date.now()
    await signIn(page, '/social')
    await expect(page.getByRole('heading', { name: 'Social Listening & Trends' })).toBeVisible()
    await expect(page.getByTestId('capture-line')).not.toContainText(/monitor|scan/i)

    await page.getByTestId('add-post-open').click()
    await page.getByTestId('post-text').fill(`מישהו מכיר מקום שמדפיס שלט עם שם לדלת? צריכה לחדר של הבת שלי (${n})`)
    await page.getByTestId('add-post').getByLabel('Group').fill('הורים בחיפה')
    await page.getByTestId('add-post-submit').click()
    const review = page.getByTestId('review')
    await expect(review.getByTestId('lead-post')).toContainText(String(n), { timeout: 60_000 })
    await expect(review.getByTestId('lead-draft')).toHaveValue(/היי!/)
    await shot(page, 'social-lead')

    const reply = `היי! נשמח להדפיס לך שלט לדלת (${n})`
    await review.getByTestId('lead-draft').fill(reply)
    await review.getByTestId('approve-copy').click()
    await expect(page.getByText('Approved and copied')).toBeVisible()
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(reply)
    await review.getByTestId('mark-posted').click()
    await expect(page.getByText('Marked as posted')).toBeVisible()

    await page.getByTestId('add-post-open').click()
    await page.getByTestId('post-text').fill(`מוכר אופניים משומשים במצב מעולה, 500 שקל (${n})`)
    await page.getByTestId('add-post-submit').click()
    const sold = page.locator('article').filter({ hasText: `500 שקל (${n})` })
    await expect(sold.getByText('ignored')).toBeVisible({ timeout: 60_000 })
    await sold.getByRole('button', { name: 'Not buyer' }).click()
    await expect(sold.getByRole('button', { name: 'Not buyer' })).toHaveAttribute('aria-pressed', 'true')
  })

  test('a capture token is shown once and lets the extension send a post', async ({ page }) => {
    const n = Date.now()
    await signIn(page, '/social')
    await page.getByTestId('token-label').fill(`e2e browser ${n}`)
    await page.getByRole('button', { name: 'New token' }).click()
    const token = (await page.getByTestId('token-fresh').locator('code').textContent())!.trim()
    expect(token).toMatch(/^pfc_/)
    await page.getByRole('button', { name: 'Done' }).click()
    await expect(page.getByTestId('token-fresh')).toHaveCount(0)

    const denied = await page.request.post('/api/v1/social/captures', { data: { post_text: 'x' } })
    expect(denied.status()).toBe(401)
    const sent = await page.request.post('/api/v1/social/captures', {
      headers: { Authorization: `Bearer ${token}` },
      data: { post_text: `ממליצים על מישהו שעושה הדפסות תלת מימד בהזמנה אישית? (${n})`, group_name: 'TCG IL',
        post_url: 'https://www.facebook.com/groups/1/posts/9' },
    })
    expect(sent.status()).toBe(202)
    await page.reload()
    await expect(page.locator('article').filter({ hasText: String(n) }).getByText(`extension:e2e browser ${n}`)).toBeVisible({ timeout: 60_000 })
  })
})

test('the Hebrew user guide opens from the sidebar and its contents links work', async ({ page }) => {
  await signIn(page, '/live')
  await page.getByTestId('nav-help').click()
  const guide = page.getByTestId('user-guide')
  await expect(guide.getByRole('heading', { level: 1 })).toHaveText('מדריך למשתמש · PrintFarm Ops')
  await expect(guide).toHaveAttribute('dir', 'rtl')
  await guide.getByRole('link', { name: 'Social Listening: סוכן הרשתות החברתיות' }).click()
  await expect(page.locator('[id="6-social-listening-סוכן-הרשתות-החברתיות"]')).toBeInViewport()
  await shot(page, 'user-guide')
})
