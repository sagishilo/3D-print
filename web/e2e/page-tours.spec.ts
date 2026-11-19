// End-to-end: the page tours (הדרכות עמוד) of every screen in a real browser against the simulator stack. Each is
// started from the "מדריך לעמוד" button in the top bar (same place on every screen); every step must highlight the
// element it names (the spotlight surrounds it, the bubble is on screen beside it), or say why it is not there now.
import { expect, type Locator, type Page, test } from '@playwright/test'

import { PAGE_TOURS, TOURS, type TourId } from '../src/lib/page-tours'
import type { TourStep } from '../src/lib/tour'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')

/** The steps admin sees: an owner is in Expert mode, so Simple-only steps are left out (as the tour does). */
const stepsOf = (id: TourId) => TOURS[id].steps.filter((s) => !s.mode || s.mode === 'expert')

const ROUTE = Object.fromEntries(PAGE_TOURS.map((id) => [id, TOURS[id].steps[0].route])) as Record<TourId, string>

/** Steps whose element depends on what the farm is doing right now (no printer waiting for clearing, no AMS, …). */
const MAY_BE_MISSING = new Set(['live-bed-cleared', 'orders-failed', 'fleet-ams', 'social-card', 'catalog-card', 'catalog-batch', 'finance-vat', 'onboarding-test'])

async function signIn(page: Page, path: string) {
  await page.goto(path)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

async function targetOf(page: Page, selectors: string[]): Promise<Locator | null> {
  for (const sel of selectors) {
    const loc = page.locator(sel).filter({ visible: true }).first()
    if (await loc.count()) return loc
  }
  return null
}

type Box = { x: number; y: number; width: number; height: number }
const contains = (o: Box, i: Box) => o.x <= i.x + 1 && o.y <= i.y + 1 && o.x + o.width >= i.x + i.width - 1 && o.y + o.height >= i.y + i.height - 1
const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

/** Waits for the step, then checks the spotlight and the bubble. Returns false when the step reported its element missing. */
async function expectStep(page: Page, tour: TourId, step: TourStep, n: number): Promise<boolean> {
  const bubble = page.getByTestId('tour-bubble')
  await expect(bubble).toHaveAttribute('data-step', step.id)
  await expect(page.getByTestId('tour-title')).toHaveText(TOURS[tour].title)
  await expect(page.getByTestId('tour-count')).toHaveText(`שלב ${n} מתוך ${stepsOf(tour).length}`)
  await expect(bubble.getByRole('heading', { level: 2 })).toHaveText(step.title)
  // Wait until the tour has decided: highlighted, or reported missing.
  await expect(bubble).toHaveAttribute('data-status', /^(anchored|missing)$/, { timeout: 10_000 })

  if ((await bubble.getAttribute('data-status')) === 'missing') {
    expect(MAY_BE_MISSING.has(step.id), `${step.id}: its element should be on the screen`).toBe(true)
    await expect(page.getByTestId('tour-missing')).toBeVisible()
    return false
  }
  const target = await targetOf(page, step.targets)
  expect(target, `${step.id}: its element is on the screen`).not.toBeNull()
  const spot = page.getByTestId('tour-spotlight')
  await expect(async () => {
    const s = (await spot.boundingBox())!
    const t = (await target!.boundingBox())!
    expect(contains(s, t), `${step.id}: spotlight ${JSON.stringify(s)} around ${JSON.stringify(t)}`).toBe(true)
  }).toPass({ timeout: 5000 })

  const view = page.viewportSize()!
  const b = (await bubble.boundingBox())!
  expect(b.x).toBeGreaterThanOrEqual(0)
  expect(b.y).toBeGreaterThanOrEqual(0)
  expect(b.x + b.width).toBeLessThanOrEqual(view.width)
  expect(b.y + b.height).toBeLessThanOrEqual(view.height)
  const side = await bubble.evaluate((el) => [...el.classList].find((c) => c.startsWith('side-')))
  if (side !== 'side-center') expect(overlaps(b, (await target!.boundingBox())!), `${step.id}: bubble beside the element`).toBe(false)
  return true
}

for (const id of PAGE_TOURS) {
  test(`the ${id} page tour highlights every part of the screen`, async ({ page }) => {
    const tour = TOURS[id]
    await signIn(page, ROUTE[id])
    const button = page.getByTestId(`page-tour-${id}`)
    await expect(button).toHaveText(/מדריך לעמוד/)
    await button.click()

    for (const [i, step] of stepsOf(id).entries()) {
      const anchored = await expectStep(page, id, step, i + 1)
      await expect(page).toHaveURL(new RegExp(`${ROUTE[id]}(\\?|$)`))
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${id}-${String(i + 1).padStart(2, '0')}-${step.id}.png` })

      // Workflow details the steps promise.
      if (step.id === 'live-actions' && anchored)
        await expect(page.locator(step.targets.at(-1)!).first().getByRole('button').first()).toBeVisible()
      if (step.id === 'live-pause-all') {
        // The page is locked during a tour: clicking the highlighted button asks nothing.
        const box = (await page.getByTestId('pause-all').boundingBox())!
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
        await expect(page.getByText('Pause all printers?')).toHaveCount(0)
      }
      if (step.id === 'orders-board')
        for (const col of ['LEAD', 'RECEIVED', 'PERSONALIZING', 'SCHEDULING', 'PRINTING', 'QC', 'SHIPPED'])
          await expect(page.getByTestId(`column-${col}`)).toBeVisible()
      if (step.id === 'fleet-drawer') await expect(page.getByTestId('printer-drawer').getByRole('tab', { name: 'Overview', selected: true })).toBeVisible()
      if (step.id === 'fleet-temps') await expect(page.getByTestId('printer-drawer').getByRole('tab', { name: 'Hardware', selected: true })).toBeVisible()
      if (step.id === 'fleet-calibration') {
        const drawer = page.getByTestId('printer-drawer')
        await expect(drawer.getByRole('tab', { name: 'Calibration', selected: true })).toBeVisible()
        await expect(drawer.getByTestId('routine-bed_level')).toBeVisible()
      }
      if (step.id === 'fleet-select') await expect(page.getByTestId('printer-drawer')).toHaveCount(0) // closed again
      if (step.id === 'fleet-batch') await expect(page.getByTestId('batch-bar').getByRole('button', { name: /Bed cleared/ })).toBeVisible()
      if (step.id === 'finance-income') await expect(page.getByRole('tab', { name: 'Income', selected: true })).toBeVisible()
      if (step.id === 'finance-settings') await expect(page.getByRole('tab', { name: 'Settings', selected: true })).toBeVisible()
      if (step.id === 'system-nats') await expect(page.getByRole('tab', { name: 'NATS', selected: true })).toBeVisible()
      if (step.id === 'catalog-batch' && anchored) await expect(page.getByTestId('queue-batch')).toBeVisible()

      await expect(page.getByTestId('tour-next')).toHaveText(i === stepsOf(id).length - 1 ? 'סיום' : 'הבא')
      await page.getByTestId('tour-next').click()
    }

    await expect(page.getByTestId('tour')).toHaveCount(0)
    // The tour leaves the screen as it found it: no drawer, no selection or batch, back on the first tab.
    await expect(page.getByTestId('printer-drawer')).toHaveCount(0)
    await expect(page.getByTestId('batch-bar')).toHaveCount(0)
    if (id === 'finance' || id === 'system') await expect(page.getByRole('tab', { name: 'Overview', selected: true })).toBeVisible()
  })
}

test('the tour button is at the same place and looks the same on every screen', async ({ page }) => {
  await signIn(page, '/live')
  await expect(page.getByTestId('page-tour-live')).toBeVisible()
  let first: { box: { x: number; y: number; width: number; height: number }; style: string } | null = null
  for (const id of PAGE_TOURS) {
    await page.goto(ROUTE[id])
    const button = page.getByTestId(`page-tour-${id}`)
    await expect(button).toBeVisible()
    await expect(page.locator('[data-testid^="page-tour-"]')).toHaveCount(1) // one button, for this screen
    await expect(button).toHaveText('?מדריך לעמוד')
    const box = (await button.boundingBox())!
    const style = await button.evaluate((el) => {
      const c = getComputedStyle(el)
      return [c.color, c.backgroundColor, c.borderColor, c.fontSize, c.height].join(' | ')
    })
    // In the top bar, at its far right.
    const bar = (await page.locator('.topbar').boundingBox())!
    expect(box.y).toBeGreaterThanOrEqual(bar.y)
    expect(box.y + box.height).toBeLessThanOrEqual(bar.y + bar.height)
    expect(bar.x + bar.width - (box.x + box.width)).toBeLessThan(40)
    if (!first) first = { box, style }
    else {
      expect(box, `${id}: same place`).toEqual(first.box)
      expect(style, `${id}: same look`).toBe(first.style)
    }
  }
})

test('leaving the fleet tour in the middle closes the panel it opened and unticks the printer', async ({ page }) => {
  await signIn(page, '/fleet')
  await page.getByTestId('page-tour-fleet').click()
  const bubble = page.getByTestId('tour-bubble')
  while ((await bubble.getAttribute('data-step')) !== 'fleet-hardware' && (await bubble.getAttribute('data-step')) !== 'fleet-temps')
    await page.getByTestId('tour-next').click()
  await expect(page.getByTestId('printer-drawer')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('tour')).toHaveCount(0)
  await expect(page.getByTestId('printer-drawer')).toHaveCount(0)

  // Back and forth over the batch step: the tick comes and goes with the tour.
  await page.getByTestId('page-tour-fleet').click()
  const at = stepsOf('fleet').findIndex((s) => s.id === 'fleet-batch')
  for (let i = 0; i < at; i++) await page.getByTestId('tour-next').click()
  await expect(bubble).toHaveAttribute('data-step', 'fleet-batch')
  await expect(page.getByTestId('batch-bar')).toBeVisible()
  await page.getByTestId('tour-close').click()
  await expect(page.getByTestId('batch-bar')).toHaveCount(0)
  await expect(page.locator('[data-tour="tile-select"] input:checked')).toHaveCount(0)
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('each page tour fits the screen', async ({ page }) => {
    await signIn(page, '/live')
    await expect(page.getByTestId('page-tour-live')).toBeVisible() // signed in
    for (const id of PAGE_TOURS) {
      if (page.url().endsWith(ROUTE[id]) === false) await page.goto(ROUTE[id])
      // On a phone the button is just the "?", still in the top bar.
      await expect(page.getByTestId(`page-tour-${id}`)).toHaveText('?', { useInnerText: true })
      await page.getByTestId(`page-tour-${id}`).click()
      for (const [i, step] of stepsOf(id).entries()) {
        await expectStep(page, id, step, i + 1)
        if (SHOTS && ['live-camera', 'orders-board', 'fleet-calibration-safety', 'social-review', 'finance-kpis', 'system-agents'].includes(step.id))
          await page.screenshot({ path: `${SHOTS}/phone-${id}-${step.id}.png` })
        await page.getByTestId('tour-next').click()
      }
      await expect(page.getByTestId('tour')).toHaveCount(0)
    }
  })
})
