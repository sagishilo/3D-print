// Display preferences of the signed-in person: theme, density and Simple / Expert mode. Saved on the gateway
// (gateway.users.prefs), so they follow the person to every device; applied to <html> as data-theme / data-density.
import { defineStore } from 'pinia'
import { computed } from 'vue'

import { api, errorText } from '@/lib/api'
import { useAuth } from '@/stores/auth'
import { useToasts } from '@/stores/toasts'

export type Theme = 'system' | 'light' | 'dark'
export type Density = 'comfortable' | 'compact'
export type Mode = 'simple' | 'expert'

export interface Prefs {
  theme?: Theme
  density?: Density
  mode?: Mode
  niche?: string // a niche slug, or 'all' (stores/niche.ts)
}

/** Owners start in Expert mode (they run the system); everyone else in Simple. */
export const defaultMode = (role: string | null): Mode => (role === 'owner' ? 'expert' : 'simple')

/** Put the theme and density on <html> (tokens.css reads them). "system" leaves the OS preference in charge. */
export function applyToDocument(p: Prefs, root: HTMLElement = document.documentElement): void {
  if (p.theme === 'light' || p.theme === 'dark') root.dataset.theme = p.theme
  else delete root.dataset.theme
  if (p.density === 'compact') root.dataset.density = 'compact'
  else delete root.dataset.density
}

export const usePrefs = defineStore('prefs', () => {
  const auth = useAuth()

  const theme = computed<Theme>(() => (auth.prefs.theme as Theme) ?? 'system')
  const density = computed<Density>(() => (auth.prefs.density as Density) ?? 'comfortable')
  const mode = computed<Mode>(() => (auth.prefs.mode as Mode) ?? defaultMode(auth.role))
  const expert = computed(() => mode.value === 'expert')

  /** Change one or more preferences: applied at once, then saved (rolled back if the gateway refuses). */
  async function set(change: Prefs): Promise<void> {
    const before = { ...auth.prefs }
    auth.prefs = { ...auth.prefs, ...change }
    try {
      const me = await api<{ prefs: Prefs }>('/api/v1/auth/prefs', { method: 'PATCH', body: change })
      auth.prefs = { ...me.prefs }
    } catch (err) {
      auth.prefs = before
      useToasts().error('Could not save the setting', errorText(err))
    }
  }

  return { theme, density, mode, expert, set }
})
