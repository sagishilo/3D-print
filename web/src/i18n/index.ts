// vue-i18n (Wave 2): English and Hebrew. Hebrew is right to left: <html dir="rtl" lang="he">. The language is a
// per-person preference (saved with the display preferences, and in this browser for the sign-in page); switching
// reloads the page so every screen renders once in the chosen language. Screens built since W2-0 use keys
// (`const { t } = useI18n()`); the older ones and the server's text are translated by ./dom.ts.
// A missing key is a bug: in development and tests it throws, in production it shows the key.
import { createI18n } from 'vue-i18n'

import { startTranslating, stopTranslating } from './dom'
import { en } from './en'
import { he } from './he'

export type Locale = 'en' | 'he'
export const LOCALES: Locale[] = ['en', 'he']
export const LOCALE_NAMES: Record<Locale, string> = { en: 'English', he: 'עברית' }
const KEY = 'pf-lang'

export const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: { en, he },
  missingWarn: false,
  fallbackWarn: false,
  missing: (locale, key) => {
    if (import.meta.env.DEV || import.meta.env.MODE === 'test') throw new Error(`i18n: no "${key}" in the ${locale} catalogue`)
    return key
  },
})

export const t = (key: string, named?: Record<string, unknown>): string =>
  named ? i18n.global.t(key, named) : i18n.global.t(key)

/** The language this browser last chose, else the browser's own (Hebrew browsers start in Hebrew). */
export function initialLocale(): Locale {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === 'en' || saved === 'he') return saved
  } catch {
    /* storage blocked: fall through */
  }
  return typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('he') ? 'he' : 'en'
}

export const currentLocale = (): Locale => i18n.global.locale.value as Locale

/** Use a language now: the catalogue, the page direction, and the translation of the older screens. */
export function applyLocale(locale: Locale, root: HTMLElement = document.documentElement): void {
  i18n.global.locale.value = locale
  root.lang = locale
  root.dir = locale === 'he' ? 'rtl' : 'ltr'
  if (locale === 'he') startTranslating(root)
  else stopTranslating()
}

/** Remember a language for this browser; reload when it changes (every screen then renders in it once). */
export function switchLocale(locale: Locale, reload = true): void {
  try {
    localStorage.setItem(KEY, locale)
  } catch {
    /* storage blocked: the choice still applies to this page */
  }
  if (locale !== currentLocale()) {
    if (reload) location.reload()
    else applyLocale(locale)
  }
}
