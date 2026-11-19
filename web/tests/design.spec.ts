// The design tokens (src/styles/tokens.css) in both themes: WCAG AA contrast for every text colour on every surface
// it is used on, 3:1 for non-text marks (niche colours), and the "system" light block identical to the explicit one.
import { describe, expect, it } from 'vitest'

import css from '../src/styles/tokens.css?raw'

type Vars = Record<string, string>

function block(selector: string, from = 0): { vars: Vars; end: number } {
  const start = css.indexOf(selector, from)
  expect(start, selector).toBeGreaterThanOrEqual(0)
  const open = css.indexOf('{', start)
  let depth = 0
  let i = open
  for (; i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}' && --depth === 0) break
  }
  const vars: Vars = {}
  for (const m of css.slice(open + 1, i).matchAll(/--([\w-]+):\s*([^;]+);/g)) vars[m[1]] = m[2].trim()
  return { vars, end: i }
}

const dark = block(':root {').vars
const lightOnly = block(":root[data-theme='light']").vars
const light: Vars = { ...dark, ...lightOnly }
const systemLight = block(':root:not([data-theme])').vars

type RGBA = [number, number, number, number]

function parse(c: string): RGBA {
  const hex = /^#([0-9a-f]{6})$/i.exec(c)
  if (hex) {
    const n = parseInt(hex[1], 16)
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1]
  }
  const rgba = /^rgba?\(([^)]+)\)$/.exec(c)
  if (rgba) {
    const [r, g, b, a = '1'] = rgba[1].split(',').map((x) => x.trim())
    return [Number(r), Number(g), Number(b), Number(a)]
  }
  throw new Error(`not a colour: ${c}`)
}

/** A translucent colour painted over an opaque one. */
function over(top: RGBA, under: RGBA): RGBA {
  const a = top[3]
  return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1]
}

function luminance([r, g, b]: RGBA): number {
  const ch = (v: number) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
}

export function contrast(a: RGBA, b: RGBA): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const THEMES: [string, Vars][] = [['dark', dark], ['light', light]]
const SURFACES = ['bg', 'surface', 'surface-2']
const TEXT = ['text', 'muted', 'accent', 'ok', 'warn', 'danger', 'idle', 'danger-text']

describe.each(THEMES)('%s theme', (_name, v) => {
  const c = (k: string) => parse(v[k])

  it('text colours reach 4.5:1 on every surface', () => {
    const fails: string[] = []
    for (const t of TEXT)
      for (const s of SURFACES) {
        const r = contrast(c(t), c(s))
        if (r < 4.5) fails.push(`${t} on ${s}: ${r.toFixed(2)}`)
      }
    expect(fails).toEqual([])
  })

  it('status text on its own tint (pills, banners) reaches 4.5:1', () => {
    const fails: string[] = []
    for (const [t, bg] of [['danger', 'danger-bg'], ['danger-text', 'danger-bg'], ['warn', 'warn-bg'], ['ok', 'ok-bg'],
      ['accent', 'accent-bg'], ['text', 'accent-bg']] as const)
      for (const s of ['surface', 'bg']) {
        const r = contrast(c(t), over(c(bg), c(s)))
        if (r < 4.5) fails.push(`${t} on ${bg} over ${s}: ${r.toFixed(2)}`)
      }
    expect(fails).toEqual([])
  })

  it('text on filled buttons and badges reaches 4.5:1', () => {
    for (const bg of ['accent', 'ok', 'danger', 'warn']) expect(contrast(c('on-accent'), c(bg)), bg).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c('on-brand'), c('brand-bambu'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c('on-brand'), c('brand-klipper'))).toBeGreaterThanOrEqual(4.5)
  })

  it('niche colours (marks next to text) reach 3:1 on the surfaces, and differ from each other', () => {
    const niches = Object.keys(v).filter((k) => k.startsWith('niche-'))
    expect(niches.length).toBe(8)
    for (const n of niches) for (const s of ['surface', 'surface-2']) expect(contrast(c(n), c(s)), `${n} on ${s}`).toBeGreaterThanOrEqual(3)
    expect(new Set(niches.map((n) => v[n])).size).toBe(8)
  })

  it('the focus ring (accent) stands out from the page: 3:1', () => {
    for (const s of SURFACES) expect(contrast(c('accent'), c(s))).toBeGreaterThanOrEqual(3)
  })
})

describe('theme files', () => {
  it('"system" light is exactly the explicit light theme', () => {
    expect(systemLight).toEqual(lightOnly)
  })

  it('every colour token of dark has a light value (none forgotten)', () => {
    const colourKeys = Object.keys(dark).filter((k) => /^(#|rgba?\()/.test(dark[k]) && !k.startsWith('brand') && k !== 'on-brand')
    const missing = colourKeys.filter((k) => !(k in lightOnly))
    expect(missing).toEqual([])
  })

  it('components use tokens, not fixed colours', () => {
    const files = import.meta.glob('../src/**/*.vue', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
    const fixed = Object.entries(files).flatMap(([path, text]) =>
      [...text.matchAll(/#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d/g)].map((m) => `${path}: ${m[0]}`))
    expect(fixed).toEqual([])
  })
})
