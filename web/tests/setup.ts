// Every component test mounts with the i18n plugin (as main.ts installs it), so `useI18n()` works everywhere.
import { config } from '@vue/test-utils'

import { i18n } from '@/i18n'

config.global.plugins = [...(config.global.plugins ?? []), i18n]
