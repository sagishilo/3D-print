// The niche the whole dashboard is focused on (top-bar switcher). Saved per person (gateway.users.prefs.niche), so
// it follows them to every device; a link with ?niche=<slug> opens a screen in that niche for this visit. Stores
// that list or report add it to their requests (withNiche) and reload when it changes.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, command, errorText } from '@/lib/api'
import { type Niche, UNASSIGNED } from '@/lib/niches'
import { useAuth } from '@/stores/auth'
import { usePrefs } from '@/stores/prefs'

export const useNiche = defineStore('niche', () => {
  const auth = useAuth()
  const prefs = usePrefs()
  const list = ref<Niche[]>([])
  const all = ref<Niche[]>([]) // archived too (Niches screen)
  const loaded = ref(false)
  const error = ref('')
  /** From a ?niche= link: wins over the saved choice until the person picks another niche. */
  const fromLink = ref<string | null>(null)

  const saved = computed(() => {
    const n = auth.prefs.niche as string | undefined
    return n && n !== 'all' ? n : null
  })
  const current = computed<string | null>(() => fromLink.value ?? saved.value)
  const bySlug = computed(() => Object.fromEntries(all.value.map((n) => [n.slug, n])) as Record<string, Niche>)
  const active = computed(() => (current.value ? bySlug.value[current.value] ?? null : null))
  /** Niches a product can be put in (not archived). */
  const choices = computed(() => list.value)

  async function load() {
    try {
      const r = await api<{ niches: Niche[] }>('/api/v1/niches?include_archived=true')
      all.value = r.niches
      list.value = r.niches.filter((n) => !n.archived)
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }

  /** Focus the dashboard on one niche (null: all niches). */
  async function set(slug: string | null) {
    fromLink.value = null
    await prefs.set({ niche: slug ?? 'all' })
  }

  function linked(slug: string | null) {
    fromLink.value = slug && slug !== 'all' ? slug : null
  }

  const name = (slug: string | null | undefined) => (slug ? bySlug.value[slug]?.name ?? slug : 'All niches')

  return {
    list, all, loaded, error, current, active, bySlug, choices, load, set, linked, name,
    upsert: (body: Record<string, unknown>) => command('catalog.niche.upsert', body),
    archive: (slug: string, archived = true, moveTo = UNASSIGNED) =>
      command('catalog.niche.archive', { slug, archived, move_to: moveTo }),
    assign: (skus: string[], niche: string) => command('catalog.niche.assign', { skus, niche }),
  }
})
