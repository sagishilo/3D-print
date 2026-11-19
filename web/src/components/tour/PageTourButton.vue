<script setup lang="ts">
// "מדריך לעמוד": starts the deep-dive tour of the current screen (lib/page-tours.ts). The top bar shows it on every
// screen, at the same place, for the tour named by the route's `tour` meta.
import type { TourId } from '@/lib/page-tours'
import { useTour } from '@/stores/tour'

const props = defineProps<{ tour: TourId }>()
const tours = useTour()
</script>

<template>
  <button
    type="button"
    class="btn page-tour"
    :data-testid="`page-tour-${props.tour}`"
    title="הדרכה מודרכת על המסך הזה: כל כפתור, מה הוא עושה ולמה"
    aria-label="מדריך לעמוד: הדרכה על המסך הזה"
    @click="tours.start(props.tour)"
  >
    <span class="q" aria-hidden="true">?</span><span class="label" lang="he" dir="rtl">מדריך לעמוד</span>
  </button>
</template>

<style scoped>
.page-tour {
  gap: 7px;
  border-color: var(--accent-line);
  color: var(--accent);
  background: var(--accent-bg);
}
.page-tour:hover {
  border-color: var(--accent);
  color: var(--accent-hover);
}
.q {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1.5px solid currentColor;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font: 700 11px var(--font);
}
/* Phones: the top bar is one row there, so just the "?" (the name stays in aria-label and title). */
@media (max-width: 600px) {
  .page-tour {
    padding: 0 9px;
  }
  .label {
    display: none;
  }
}
</style>
