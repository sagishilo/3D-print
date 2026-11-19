// Today: the ranked list of work across the farm (gateway /api/v1/today). Reloads when the live feed reports a
// change that could add or clear an item (stores/live.ts), and when the niche in the top bar changes.
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { api, command, errorText } from '@/lib/api'
import { withNiche } from '@/lib/niches'
import { type TodayItem, type TodayList, type TodaySummary, visible } from '@/lib/today'
import { useNiche } from '@/stores/niche'
import { usePrefs } from '@/stores/prefs'

export const useToday = defineStore('today', () => {
  const list = ref<TodayList | null>(null)
  const summary = ref<TodaySummary | null>(null)
  const error = ref('')
  const loaded = ref(false)
  const done = ref<Set<string>>(new Set())      // acted on here: hidden until the next answer drops them
  const niche = useNiche()
  const prefs = usePrefs()
  let timer: ReturnType<typeof setTimeout> | null = null

  async function load() {
    try {
      const [l, s] = await Promise.all([
        api<TodayList>(withNiche('/api/v1/today', niche.current)),
        api<TodaySummary>(withNiche('/api/v1/today/summary', niche.current)).catch(() => null),   // the strip is extra: the list still shows
      ])
      list.value = l
      summary.value = s && s.printers && s.alerts && Array.isArray(s.week) ? s : null      // an older gateway: no strip
      done.value = new Set()
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loaded.value = true
    }
  }
  watch(() => niche.current, () => {
    if (loaded.value) void load()
  })

  function reloadSoon(ms = 600) {
    if (!loaded.value) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), ms)
  }

  const groups = computed(() => {
    const g = visible(list.value, prefs.expert)
    const skip = (items: TodayItem[]) => items.filter((i) => !done.value.has(i.id))
    return { now: skip(g.now), today: skip(g.today), week: skip(g.week) }
  })
  const count = computed(() => groups.value.now.length + groups.value.today.length + groups.value.week.length)
  const urgent = computed(() => groups.value.now.length)

  /** Run an item's command; the item hides at once and the list reloads when the agent has answered. */
  async function act(item: TodayItem) {
    if (item.action.type !== 'command') return
    await command(item.action.name, item.action.body)
    done.value = new Set([...done.value, item.id])
    reloadSoon(1500)
  }

  return { list, summary, error, loaded, groups, count, urgent, load, reloadSoon, act }
})
