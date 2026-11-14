// End-to-end: the dashboard in a real browser against the simulator stack (see playwright.config.ts).
import { execFileSync } from 'node:child_process'

import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const PROJECT = process.env.PRINTFARM_COMPOSE_PROJECT ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

async function signIn(page: Page, path = '/live') {
  await page.goto(path)
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
  for (const agent of ['coordinator', 'telemetry', 'fleet', 'personalization', 'inventory', 'finance']) {
    await expect(page.getByTestId(`agent-${agent}`)).toHaveAttribute('data-status', 'healthy', { timeout: 40_000 })
  }
  await expect(page.getByTestId('agent-social')).toContainText('Planned')
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
  await expect(page.getByText('Last change: Auto-reorder filament disabled by admin')).toBeVisible()
  await shot(page, '5-system-config')
  await reorder.click()
  await expect(page.getByText('Last change: Auto-reorder filament enabled by admin')).toBeVisible()

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
    await signIn(page, '/fleet')
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
