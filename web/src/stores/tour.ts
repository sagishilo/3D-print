// The guided tour's state: which step is open. The overlay (components/tour/TourOverlay.vue) follows it, moves to the
// step's screen and highlights its element.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { TOUR_STEPS, type TourStep } from '@/lib/tour'

const DONE_KEY = 'pf.tour.done'

export const useTour = defineStore('tour', () => {
  const steps = ref<TourStep[]>(TOUR_STEPS)
  const index = ref<number | null>(null)

  const active = computed(() => index.value !== null)
  const step = computed(() => (index.value === null ? null : steps.value[index.value]))
  const isFirst = computed(() => index.value === 0)
  const isLast = computed(() => index.value === steps.value.length - 1)

  function start(at = 0) {
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
      localStorage.setItem(DONE_KEY, new Date().toISOString())
    } catch {
      /* private window: nothing to remember */
    }
  }
  function seen(): boolean {
    try {
      return !!localStorage.getItem(DONE_KEY)
    } catch {
      return false
    }
  }

  return { steps, index, active, step, isFirst, isLast, start, next, prev, stop, finish, seen }
})
