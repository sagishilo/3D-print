<script setup lang="ts">
// "מדריך לעמוד": starts the deep-dive tour of the screen it sits on (lib/page-tours.ts).
import type { TourId } from '@/lib/page-tours'
import { useTour } from '@/stores/tour'

const props = defineProps<{ tour: TourId }>()
const tours = useTour()
</script>

<template>
  <button
    type="button"
    class="btn btn-sm page-tour"
    :data-testid="`page-tour-${props.tour}`"
    title="הדרכה מודרכת על המסך הזה: כל כפתור, מה הוא עושה ולמה"
    @click="tours.start(props.tour)"
  >
    <span class="q" aria-hidden="true">?</span><span lang="he" dir="rtl">מדריך לעמוד</span>
  </button>
</template>

<style scoped>
.page-tour {
  gap: 7px;
  border-color: rgba(76, 141, 255, 0.45);
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
</style>
