// vue-i18n (Wave 2 / W2-0): one plugin, English now; W2-D adds Hebrew and right-to-left (a per-person language, saved
// with the display preferences). Components use `const { t } = useI18n()`; code outside components uses `t` below.
// A missing key is a bug: in development and tests it throws, in production it shows the key.
import { createI18n } from 'vue-i18n'

import { en } from './en'

export type Locale = 'en'
export const LOCALES: Locale[] = ['en']

export const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: { en },
  missingWarn: false,
  fallbackWarn: false,
  missing: (locale, key) => {
    if (import.meta.env.DEV || import.meta.env.MODE === 'test') throw new Error(`i18n: no "${key}" in the ${locale} catalogue`)
    return key
  },
})

export const t = (key: string, named?: Record<string, unknown>): string =>
  named ? i18n.global.t(key, named) : i18n.global.t(key)
