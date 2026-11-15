<script setup lang="ts">
// מדריך למשתמש: docs/USER_GUIDE_HE.md, built into the dashboard (works without internet). Links inside the guide's
// table of contents scroll to their section.
import { computed } from 'vue'

import guide from '../../../docs/USER_GUIDE_HE.md?raw'

import { renderGuide } from '@/lib/markdown'
import { useTour } from '@/stores/tour'

const html = renderGuide(guide)
const tour = useTour()
const seen = computed(() => !tour.active && tour.seen())

function onClick(e: MouseEvent) {
  const a = (e.target as HTMLElement).closest('a')
  const href = a?.getAttribute('href') ?? ''
  if (!href.startsWith('#')) return
  e.preventDefault()
  document.getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <section class="tour-card" dir="rtl" lang="he" aria-labelledby="tour-card-title">
    <div class="tour-icon" aria-hidden="true">🧭</div>
    <div class="tour-text">
      <h2 id="tour-card-title">חדשים כאן? בואו נעשה סיור</h2>
      <p>סיור מודרך של כ-2 דקות על המסכים האמיתיים: איפה כל דבר נמצא, מה כל כפתור עושה, ולמה. במהלך הסיור הכפתורים נעולים, כך שאי אפשר לשנות כלום בטעות.</p>
    </div>
    <button type="button" class="btn btn-primary start" data-testid="tour-start" @click="tour.start()">
      {{ seen ? 'התחל את הסיור שוב' : 'התחל סיור מודרך' }}
    </button>
  </section>
  <!-- eslint-disable-next-line vue/no-v-html -- our own guide, compiled into the build -->
  <article class="guide" dir="rtl" lang="he" data-testid="user-guide" @click="onClick" v-html="html" />
</template>

<style scoped>
.tour-card {
  max-width: 880px;
  width: 100%;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 20px;
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  background: linear-gradient(120deg, var(--accent-bg), transparent 70%), var(--surface);
}
.tour-icon {
  font-size: 30px;
  line-height: 1;
}
.tour-text {
  flex: 1;
  min-width: 0;
}
.tour-text h2 {
  font: 700 18px var(--font-display);
  margin: 0 0 4px;
}
.tour-text p {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.6;
}
.start {
  white-space: nowrap;
  min-height: 44px;
  padding: 0 20px;
  font-size: 15px;
}
@media (max-width: 600px) {
  .tour-card {
    flex-direction: column;
    align-items: stretch;
    text-align: start;
  }
  .tour-icon {
    display: none;
  }
}
.guide {
  max-width: 880px;
  margin: 0 auto;
  line-height: 1.7;
  font-size: 15px;
}
.guide :deep(h1) {
  font: 700 26px var(--font-display);
  margin: 0 0 12px;
}
.guide :deep(h2) {
  font: 700 20px var(--font-display);
  margin: 32px 0 10px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  scroll-margin-top: 16px;
}
.guide :deep(h3) {
  font: 600 16px var(--font);
  margin: 20px 0 6px;
  scroll-margin-top: 16px;
}
.guide :deep(p),
.guide :deep(ul),
.guide :deep(ol) {
  margin: 8px 0;
}
.guide :deep(ul),
.guide :deep(ol) {
  padding-inline-start: 22px;
}
.guide :deep(li) {
  margin: 3px 0;
}
.guide :deep(strong) {
  color: var(--text);
}
.guide :deep(a) {
  color: var(--accent);
}
.guide :deep(code) {
  font: 13px var(--font-mono);
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  direction: ltr;
  unicode-bidi: isolate;
}
.guide :deep(blockquote) {
  margin: 12px 0;
  padding: 10px 14px;
  border-inline-start: 3px solid var(--accent);
  background: var(--accent-bg);
  border-radius: var(--radius-sm);
}
.guide :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 14px;
  display: block;
  overflow-x: auto;
}
.guide :deep(th),
.guide :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: start;
  vertical-align: top;
}
.guide :deep(th) {
  background: var(--surface);
}
.guide :deep(hr) {
  border: 0;
  border-top: 1px solid var(--border);
  margin: 24px 0;
}
</style>
