// End-to-end: the guided tour (סיור מודרך) in a real browser against the simulator stack. It starts from the help page,
// clicks through every step and checks that each bubble is anchored to the real element on the screen: the spotlight
// surrounds the element, the bubble is on screen beside it, and the page underneath does not take clicks.
import { expect, type Locator, type Page, test } from '@playwright/test'

import { TOUR_STEPS } from '../src/lib/tour'

const PASSWORD = process.env.PRINTFARM_PASSWORD ?? ''
const SHOTS = process.env.PW_SCREENSHOTS ?? ''

test.skip(!PASSWORD, 'set PRINTFARM_PASSWORD to the admin password of the running stack')

async function signIn(page: Page, path: string) {
  await page.goto(path)
  await page.locator('#pass').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

/** The element a step highlights: the first of its selectors with a visible match (as the tour itself picks). */
async function targetOf(page: Page, selectors: string[]): Promise<Locator | null> {
  for (const sel of selectors) {
    const loc = page.locator(sel).filter({ visible: true }).first()
    if (await loc.count()) return loc
  }
  return null
}

type Box = { x: number; y: number; width: number; height: number }
const contains = (outer: Box, inner: Box) =>
  outer.x <= inner.x + 1 && outer.y <= inner.y + 1 && outer.x + outer.width >= inner.x + inner.width - 1 && outer.y + outer.height >= inner.y + inner.height - 1
const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

async function expectAnchored(page: Page, step: (typeof TOUR_STEPS)[number]) {
  const bubble = page.getByTestId('tour-bubble')
  await expect(bubble).toHaveAttribute('data-step', step.id)
  await expect(bubble).toHaveAttribute('dir', 'rtl')
  await expect(bubble.getByRole('heading', { level: 2 })).toHaveText(step.title)
  const view = page.viewportSize()!

  if (!step.targets.length) {
    await expect(bubble).toHaveAttribute('data-anchored', 'false')
    return
  }
  await expect(bubble).toHaveAttribute('data-anchored', 'true')
  await expect(page.getByTestId('tour-missing')).toHaveCount(0)
  const target = await targetOf(page, step.targets)
  expect(target, `${step.id}: its element is on the screen`).not.toBeNull()

  // The spotlight settles (it slides between steps), then surrounds the element.
  const spot = page.getByTestId('tour-spotlight')
  await expect(async () => {
    const s = (await spot.boundingBox())!
    const t = (await target!.boundingBox())!
    expect(contains(s, t), `${step.id}: spotlight ${JSON.stringify(s)} around ${JSON.stringify(t)}`).toBe(true)
  }).toPass({ timeout: 5000 })

  // The bubble is fully on screen and, when there is room, beside the element rather than over it.
  const b = (await bubble.boundingBox())!
  expect(b.x).toBeGreaterThanOrEqual(0)
  expect(b.y).toBeGreaterThanOrEqual(0)
  expect(b.x + b.width).toBeLessThanOrEqual(view.width)
  expect(b.y + b.height).toBeLessThanOrEqual(view.height)
  const t = (await target!.boundingBox())!
  const side = await bubble.evaluate((el) => [...el.classList].find((c) => c.startsWith('side-')))
  if (side !== 'side-center') expect(overlaps(b, t), `${step.id}: bubble beside the element`).toBe(false)
}

test('the guided tour walks through every step, anchored to the real dashboard', async ({ page }) => {
  await signIn(page, '/help')
  const start = page.getByTestId('tour-start')
  await expect(start).toBeVisible()
  await page.evaluate(() => localStorage.removeItem('pf.tour.done'))
  await page.reload()
  await expect(start).toHaveText('התחל סיור מודרך')
  await start.click()

  const bubble = page.getByTestId('tour-bubble')
  for (const [i, step] of TOUR_STEPS.entries()) {
    await expect(page.getByTestId('tour-count')).toHaveText(`שלב ${i + 1} מתוך ${TOUR_STEPS.length}`)
    await expect(page).toHaveURL(new RegExp(`${step.route}(\\?|$)`))
    await expectAnchored(page, step)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/tour-${i + 1}-${step.id}.png` })

    if (step.id === 'pause-all') {
      // The page is locked during the tour: a click on the highlighted Pause all does not ask to pause anything.
      const box = (await page.getByTestId('pause-all').boundingBox())!
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
      await expect(page.getByText('Pause all printers?')).toHaveCount(0)
      await expect(bubble).toHaveAttribute('data-step', 'pause-all')
    }
    if (step.id === 'printer-tile') {
      // The highlighted tile is a real printer, and one with Pause / Resume / Bed cleared on it when there is one.
      const tile = (await targetOf(page, step.targets))!
      await expect(tile).toHaveAttribute('data-testid', /^printer-/)
      const withActions = page.locator('[data-tour="printer-tile"]').filter({ has: page.getByRole('button', { name: /^(Pause|Resume|Bed of) / }) })
      if (await withActions.count()) await expect(tile.getByRole('button', { name: /^(Pause|Resume|Bed of) / }).first()).toBeVisible()
    }

    if (i > 0 && i < TOUR_STEPS.length - 1 && i % 3 === 0) {
      // Back and forth works.
      await page.getByTestId('tour-prev').click()
      await expect(bubble).toHaveAttribute('data-step', TOUR_STEPS[i - 1].id)
      await page.getByTestId('tour-next').click()
      await expect(bubble).toHaveAttribute('data-step', step.id)
    }
    await expect(page.getByTestId('tour-next')).toHaveText(i === TOUR_STEPS.length - 1 ? 'סיום' : 'הבא')
    await page.getByTestId('tour-next').click()
  }

  await expect(page.getByTestId('tour')).toHaveCount(0)
  await expect(page).toHaveURL(/\/help$/)
  await expect(start).toHaveText('התחל את הסיור שוב')
})

test('the tour is closed with Escape and the dashboard works again', async ({ page }) => {
  await signIn(page, '/help')
  await page.getByTestId('tour-start').click()
  await page.getByTestId('tour-next').click()
  await expect(page.getByTestId('tour-bubble')).toHaveAttribute('data-step', 'sidebar')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('tour')).toHaveCount(0)
  await page.getByRole('link', { name: 'Order Pipeline' }).click()
  await expect(page).toHaveURL(/\/orders$/)
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the tour opens the sidebar for its step and every bubble fits the screen', async ({ page }) => {
    await signIn(page, '/help')
    await page.getByTestId('tour-start').click()
    for (const [i, step] of TOUR_STEPS.entries()) {
      await expectAnchored(page, step)
      if (SHOTS && ['sidebar', 'printer-tile', 'social'].includes(step.id)) await page.screenshot({ path: `${SHOTS}/tour-phone-${i + 1}-${step.id}.png` })
      await page.getByTestId('tour-next').click()
    }
    await expect(page.getByTestId('tour')).toHaveCount(0)
  })
})
