// W2-D: the dashboard in Hebrew. The dictionary covers every text of every template, every UI string built in
// scripts and every server pattern; he.ts has exactly the keys of en.ts; the translator handles exact text, patterns
// with values, values that are UI words themselves, whitespace, and leaves names, numbers and Hebrew alone; the page
// is translated as Vue renders and updates it (translate="no" is left alone); the language switch sets RTL.
// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'

import { applyLocale, i18n } from '@/i18n'
import { HE_UI, startTranslating, stopTranslating, translate } from '@/i18n/dom'
import { en } from '@/i18n/en'
import { he } from '@/i18n/he'
import serverText from '@/i18n/server-text.json'

// @ts-expect-error - plain .mjs helpers shared with the command line
import { scriptText } from '../scripts/script-text.mjs'
// @ts-expect-error - plain .mjs helpers shared with the command line
import { uiText } from '../scripts/ui-text.mjs'

const HEBREW = /[֐-׿]/
const HEBREW_FIRST = /PrinterOnboardingView\.vue$/          // written in Hebrew; its English bits are product names

const norm = (s: string) => s.replace(/\s+/g, ' ').trim()
const known = (s: string) => norm(s) in HE_UI
const needs = (s: string) => /[A-Za-z]{2}/.test(s.replace(/\{\w+\}/g, '')) && !HEBREW.test(s)

describe('the Hebrew dictionary', () => {
  it('covers every text of every template', () => {
    const missing = [...new Set((uiText('src') as [string, string][])
      .filter(([s, file]) => !HEBREW_FIRST.test(file) && needs(s) && !known(s)).map(([s]) => s))]
    expect(missing).toEqual([])
  })

  it('covers the UI text built in scripts', () => {
    const missing = [...new Set((scriptText('src') as [string, string][]).filter(([s]) => needs(s) && !known(s)).map(([s]) => s))]
    expect(missing).toEqual([])
  })

  it('covers the text the server sends', () => {
    expect((serverText as string[]).filter((s) => needs(s) && !known(s))).toEqual([])
  })

  it('keeps the placeholders of each entry', () => {
    const bad = Object.entries(HE_UI).filter(([k, v]) => {
      const numbered = (s: string) => new Set(s.match(/\{\d+\}/g) ?? [])
      return [...numbered(v)].some((p) => !numbered(k).has(p))           // Hebrew may drop a plural "s", never invent one
    })
    expect(bad).toEqual([])
  })
})

describe('the Hebrew catalogue', () => {
  const keys = (o: object, prefix = ''): string[] =>
    Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`]))

  it('has exactly the keys of the English one, with the same {placeholders}', () => {
    expect(keys(he).sort()).toEqual(keys(en).sort())
    const flat = (o: object, p = ''): [string, string][] =>
      Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' ? flat(v, `${p}${k}.`) : [[`${p}${k}`, String(v)]]))
    const heMap = Object.fromEntries(flat(he))
    const names = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join()
    expect(flat(en).filter(([k, v]) => names(v) !== names(heMap[k])).map(([k]) => k)).toEqual([])
  })
})

describe('translate', () => {
  const dict = {
    Cancel: 'ביטול', 'Order {0} is waiting in {1}': 'הזמנה {0} ממתינה ב-{1}', Personalization: 'התאמה אישית',
    '{0} store order{1} wait{2} for you': '{0} הזמנות מהחנות ממתינות לכם', '{0} g': '{0} גרם',
  }

  it('exact text, patterns, values that are UI words, whitespace', () => {
    expect(translate('Cancel', dict)).toBe('ביטול')
    expect(translate('  Cancel \n', dict)).toBe('  ביטול \n')
    expect(translate('Order ord-1 is waiting in Personalization', dict)).toBe('הזמנה ord-1 ממתינה ב-התאמה אישית')
    expect(translate('3 store orders wait for you', dict)).toBe('3 הזמנות מהחנות ממתינות לכם')
    expect(translate('1 store order waits for you', dict)).toBe('1 הזמנות מהחנות ממתינות לכם')
  })

  it('leaves names, numbers, Hebrew and unknown text alone', () => {
    for (const s of ['Noa Levi', '42', '₪12.00', 'שלום', 'RR123456789IL', '']) expect(translate(s, dict)).toBeNull()
    expect(translate('12 g', dict)).toBeNull()                   // a pattern with almost no words of its own is not used
    // a short pattern never swallows a sentence: "{0} of {1}" is not "below the minimum of 1000 g"
    expect(translate('PLA black: 987 g left, below the minimum of 1000 g', { '{0} of {1}': '{0} מתוך {1}' })).toBeNull()
    expect(translate('3 of 5', { '{0} of {1}': '{0} מתוך {1}' })).toBe('3 מתוך 5')         // single values: fine
    // a phrase value that is UI text itself is taken, and translated too
    expect(translate('Nov 2026 vs Oct 2026', { '{0} vs {1}': '{0} מול {1}', 'Nov {0}': 'נוב׳ {0}', 'Oct {0}': 'אוק׳ {0}' }))
      .toBe('נוב׳ 2026 מול אוק׳ 2026')
    expect(translate('Order ord-1 is waiting in Personalization', { ...dict, '{0} of {1}': 'x' })).toBe('הזמנה ord-1 ממתינה ב-התאמה אישית')
  })
})

describe('the page in Hebrew', () => {
  afterEach(() => {
    stopTranslating()
    applyLocale('en')
  })

  it('translates what Vue renders and updates, attributes too, but not translate="no"', async () => {
    const stage = ref('Cancel')
    const C = defineComponent(() => () => h('div', [
      h('button', { title: 'Close' }, stage.value),
      h('span', { translate: 'no' }, 'Cancel'),
      h('input', { placeholder: 'Customer name' }),
    ]))
    const w = mount(C, { attachTo: document.body })
    applyLocale('he')
    expect(document.documentElement.dir).toBe('rtl')
    expect(document.documentElement.lang).toBe('he')
    expect(i18n.global.locale.value).toBe('he')
    expect(w.get('button').text()).toBe('ביטול')
    expect(w.get('button').attributes('title')).toBe('סגירה')
    expect(w.get('span').text()).toBe('Cancel')
    expect(w.get('input').attributes('placeholder')).toBe('שם הלקוח')
    stage.value = 'Save'
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))                   // the observer runs after the DOM change
    expect(w.get('button').text()).toBe('שמירה')
    w.unmount()
  })

  it('switching back to English sets the page left to right', () => {
    applyLocale('he')
    applyLocale('en')
    expect(document.documentElement.dir).toBe('ltr')
    startTranslating()
    stopTranslating()
  })
})
