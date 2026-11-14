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
