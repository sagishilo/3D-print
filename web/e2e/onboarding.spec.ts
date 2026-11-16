// End-to-end: connecting a printer (חיבור מדפסת חדשה) on the simulator stack. The connection tests are real: the
// gateway logs in to the simulated Bambu printer's MQTT, waits for its report, opens its FTPS port and reads a frame
// from its port-6000 camera; for Klipper it queries the simulated Moonraker.
import { expect, type Page, test } from '@playwright/test'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')

async function signIn(page: Page, path: string) {
  await page.goto(path)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/onboarding-${name}.png`, fullPage: true })
}

test('a Bambu printer (simulated) is connected from Printer Fleet, tested for real, and its config written out', async ({ page }) => {
  await signIn(page, '/fleet')
  await page.getByTestId('add-printer').click()
  await expect(page).toHaveURL(/\/fleet\/add$/)
  await expect(page.getByRole('heading', { name: 'Connect a new printer' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Printer Fleet' }).first()).toHaveClass(/active/)   // sidebar keeps its place
  await shot(page, '1-type')

  await page.getByTestId('vendor-bambu').click()
  await expect(page.getByTestId('guide-step')).toContainText('LAN Only Mode')
  await expect(page.getByTestId('onboarding-step-1')).toHaveClass(/on/)
  await page.getByTestId('step-next').click()
  await expect(page.getByTestId('guide-step')).toContainText('Access Code')
  await page.getByTestId('step-next').click()

  // Validation before anything is sent.
  const next = page.getByTestId('step-next')
  await expect(next).toBeDisabled()
  await page.getByTestId('f-id').fill('Bad Name')
  await page.getByTestId('f-code').fill('123')
  await page.getByTestId('test-connection').click()
  await expect(page.getByTestId('form-invalid')).toBeVisible()
  await expect(page.getByText('קוד הגישה: בדיוק 8 תווים')).toBeVisible()
  await expect(page.getByTestId('test-result')).toHaveCount(0)

  // The practice printer: the simulator's MQTT broker, FTPS and camera ports.
  await page.getByTestId('use-simulator').click()
  await expect(page.getByTestId('f-host')).toHaveValue('mosquitto')
  await page.getByTestId('test-connection').click()
  const result = page.getByTestId('test-result')
  await expect(result).toHaveAttribute('data-ok', 'true', { timeout: 30_000 })
  for (const key of ['network', 'login', 'telemetry', 'upload', 'camera'])
    await expect(page.getByTestId(`check-${key}`), key).toHaveAttribute('data-ok', 'true')
  await expect(page.getByTestId('check-telemetry')).toContainText(/state \w+ · nozzle \d+ °C/)
  await expect(page.getByTestId('check-camera')).toContainText('picture received')
  await shot(page, '2-test-passed')

  // Changing a detail asks for a new test.
  await page.getByTestId('f-id').fill('practice-p1-b')
  await expect(result).toHaveCount(0)
  await expect(next).toBeDisabled()
  await page.getByTestId('test-connection').click()
  await expect(result).toHaveAttribute('data-ok', 'true', { timeout: 30_000 })

  await next.click()
  const add = page.getByTestId('add-step')
  await expect(add).toContainText('practice-p1-b')
  await expect(page.getByTestId('practice-note')).toBeVisible()   // practice lines are an example, not to paste
  await expect(page.getByTestId('snippet-0')).toContainText('access_code_env = "BAMBU_CODE_PRACTICE_P1_B"')
  await expect(page.getByTestId('snippet-0')).not.toContainText('12345678')       // the code is not in fleet.toml
  await expect(page.getByTestId('snippet-1')).toContainText('BAMBU_CODE_PRACTICE_P1_B=12345678')
  await expect(page.getByTestId('snippet-commands')).toContainText('docker compose restart telemetry fleet go2rtc')
  await page.getByTestId('copy-0').click()
  await expect(page.getByText('הועתק')).toBeVisible()
  await shot(page, '3-add')
  await page.getByTestId('finish').click()
  await expect(page).toHaveURL(/\/fleet$/)
})

test('a Klipper printer (simulated) passes through Moonraker', async ({ page }) => {
  await signIn(page, '/fleet/add')
  await page.getByTestId('vendor-moonraker').click()
  await expect(page.getByTestId('guide-step')).toContainText('Raspberry Pi')
  await page.getByTestId('step-next').click()
  await expect(page.getByTestId('guide-step')).toContainText('7125')
  await page.getByTestId('step-next').click()
  await page.getByTestId('use-simulator').click()
  await page.getByTestId('test-connection').click()
  await expect(page.getByTestId('test-result')).toHaveAttribute('data-ok', 'true', { timeout: 30_000 })
  await expect(page.getByTestId('check-api')).toContainText('Klipper ready')
  await expect(page.getByTestId('check-telemetry')).toContainText('nozzle')
  await page.getByTestId('step-next').click()
  await expect(page.getByTestId('snippet-0')).toContainText('url = "ws://simulator:7125/websocket"')
})

test('a printer that does not answer fails with a fix, and an internet address is refused', async ({ page }) => {
  await signIn(page, '/fleet/add')
  await page.getByTestId('vendor-moonraker').click()
  await page.getByTestId('step-next').click()
  await page.getByTestId('step-next').click()
  await page.getByTestId('f-id').fill('voron-x')
  await page.getByTestId('f-host').fill('simulator')
  await page.getByTestId('f-port').fill('7999')                           // nothing listens there
  await page.getByTestId('test-connection').click()
  await expect(page.getByTestId('test-result')).toHaveAttribute('data-ok', 'false', { timeout: 30_000 })
  await expect(page.getByTestId('check-network')).toHaveAttribute('data-ok', 'false')
  await expect(page.getByTestId('check-network')).toContainText('באותה רשת')
  await expect(page.getByTestId('step-next')).toBeDisabled()
  await shot(page, '4-test-failed')

  await page.getByTestId('f-host').fill('8.8.8.8')
  await page.getByTestId('f-port').fill('7125')
  await page.getByTestId('test-connection').click()
  await expect(page.getByTestId('test-error')).toContainText('not on the local network')
})

test('a generic printer explains what the farm can do with it', async ({ page }) => {
  await signIn(page, '/fleet/add')
  await page.getByTestId('vendor-generic').click()
  await expect(page.getByTestId('guide-step')).toContainText('Klipper / Voron')
  await page.getByTestId('step-back').click()
  await expect(page.getByTestId('vendor-bambu')).toBeVisible()
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the hub fits the screen', async ({ page }) => {
    await signIn(page, '/fleet/add')
    await page.getByTestId('vendor-bambu').click()
    await page.getByTestId('step-next').click()
    await page.getByTestId('step-next').click()
    await page.getByTestId('use-simulator').click()
    await page.getByTestId('test-connection').click()
    await expect(page.getByTestId('test-result')).toHaveAttribute('data-ok', 'true', { timeout: 30_000 })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
    await shot(page, 'phone')
  })
})
