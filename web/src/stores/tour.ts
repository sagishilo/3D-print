// The tours' state: which tour is open (the general tour or a screen's deep dive, lib/page-tours.ts) and which step.
// The overlay (components/tour/TourOverlay.vue) follows it, moves to the step's screen and highlights its element.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { type TourId, TOURS } from '@/lib/page-tours'
import { usePrefs } from '@/stores/prefs'

const doneKey = (id: TourId) => (id === 'general' ? 'pf.tour.done' : `pf.tour.${id}.done`)

export const useTour = defineStore('tour', () => {
  const prefs = usePrefs()
  const tourId = ref<TourId>('general')
  const index = ref<number | null>(null)

  const tour = computed(() => TOURS[tourId.value])
  // Steps for the other display mode are left out (Simple hides the panels some steps point at).
  const steps = computed(() => tour.value.steps.filter((s) => !s.mode || s.mode === prefs.mode))
  const title = computed(() => tour.value.title)
  const active = computed(() => index.value !== null)
  const step = computed(() => (index.value === null ? null : steps.value[index.value]))
  const isFirst = computed(() => index.value === 0)
  const isLast = computed(() => index.value === steps.value.length - 1)

  function start(id: TourId = 'general', at = 0) {
    tourId.value = id
    index.value = Math.max(0, Math.min(at, steps.value.length - 1))
  }
  function next() {
    if (index.value === null) return
    if (isLast.value) return finish()
    index.value += 1
  }
  function prev() {
    if (index.value !== null && index.value > 0) index.value -= 1
  }
  function stop() {
    index.value = null
  }
  function finish() {
    index.value = null
    try {
      localStorage.setItem(doneKey(tourId.value), new Date().toISOString())
    } catch {
      /* private window: nothing to remember */
    }
  }
  function seen(id: TourId = 'general'): boolean {
    try {
      return !!localStorage.getItem(doneKey(id))
    } catch {
      return false
    }
  }

  return { tourId, index, title, steps, active, step, isFirst, isLast, start, next, prev, stop, finish, seen }
})
