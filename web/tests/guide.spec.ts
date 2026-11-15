import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import guide from '../../docs/USER_GUIDE_HE.md?raw'

import { renderGuide, REPO_URL, resolveLink, slug } from '@/lib/markdown'
import HelpView from '@/views/HelpView.vue'

describe('user guide rendering', () => {
  it('makes GitHub-style anchors, Hebrew included', () => {
    expect(slug('1. התחברות למערכת')).toBe('1-התחברות-למערכת')
    expect(slug('6. Social Listening: סוכן הרשתות החברתיות')).toBe('6-social-listening-סוכן-הרשתות-החברתיות')
    expect(slug('3. ניהול הזמנות (Order Pipeline)')).toBe('3-ניהול-הזמנות-order-pipeline')
  })

  it('sends links to other repository files to GitHub and leaves anchors alone', () => {
    expect(resolveLink('../README.md')).toBe(`${REPO_URL}/README.md`)
    expect(resolveLink('DEVELOPMENT.md')).toBe(`${REPO_URL}/docs/DEVELOPMENT.md`)
    expect(resolveLink('#2-סקירת-הדשבורד')).toBe('#2-סקירת-הדשבורד')
    expect(resolveLink('https://localhost')).toBe('https://localhost')
  })

  it('every table-of-contents link of the guide reaches a heading', () => {
    const html = renderGuide(guide)
    const ids = new Set([...html.matchAll(/<h\d id="([^"]+)"/g)].map((m) => m[1]))
    const anchors = [...guide.matchAll(/\]\(#([^)]+)\)/g)].map((m) => m[1])
    expect(anchors.length).toBeGreaterThanOrEqual(8)
    for (const a of anchors) expect(ids, a).toContain(a)
  })

  it('covers the topics operators asked for', () => {
    for (const must of ['https://localhost', 'Advanced', 'Sell', 'Make', 'Business', 'Admin', 'Live · 0.4 s ago', 'Order Pipeline',
      'Confirm order', 'Bed cleared', 'Pause', 'Resume', 'Cancel print', 'Import 3MF', 'Queue batch', 'Capture post to PrintFarm',
      'Add post', 'Approve & Copy', 'Mark as posted', '**J**', '**K**', '**A**', '**P**', '**R**']) {
      expect(guide, must).toContain(must)
    }
  })

  it('renders right-to-left in the dashboard', () => {
    const w = mount(HelpView)
    const el = w.get('[data-testid=user-guide]')
    expect(el.attributes('dir')).toBe('rtl')
    expect(el.find('h1').text()).toBe('מדריך למשתמש · PrintFarm Ops')
    expect(el.findAll('table').length).toBeGreaterThan(3)
  })
})
