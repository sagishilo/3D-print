<script setup lang="ts">
// The guided tour on screen: dims the page, cuts a spotlight around the step's element and puts a speech bubble next to
// it. While the tour is open the page underneath does not take clicks, so nothing can be changed by accident.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { findTarget, placeBubble, richText, type Placement, type Rect } from '@/lib/tour'
import { useTour } from '@/stores/tour'

const tour = useTour()
const router = useRouter()

const PAD = 8
const rect = ref<Rect | null>(null)
const missing = ref(false)
const searching = ref(false)
const bubble = ref<HTMLElement | null>(null)
const nextBtn = ref<HTMLButtonElement | null>(null)
const placement = ref<Placement>({ top: 0, left: 0, side: 'center' })

let target: HTMLElement | null = null
let frame = 0
let token = 0
let scrolledAt = 0

const total = computed(() => tour.steps.length)
const n = computed(() => (tour.index ?? 0) + 1)

function measure() {
  if (target && !target.isConnected) target = null
  const r = target?.getBoundingClientRect()
  // A screen that is still loading can push the element out of view after it was scrolled to: bring it back.
  if (target && r && (r.bottom < 0 || r.top > window.innerHeight) && performance.now() - scrolledAt > 300) {
    scrolledAt = performance.now()
    target.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' as ScrollBehavior })
  }
  const next: Rect | null = r ? { top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 } : null
  const cur = rect.value
  if (!next !== !cur || (next && cur && (next.top !== cur.top || next.left !== cur.left || next.width !== cur.width || next.height !== cur.height)))
    rect.value = next
  const b = bubble.value
  if (b) {
    const p = placeBubble(rect.value, { width: b.offsetWidth, height: b.offsetHeight }, { width: window.innerWidth, height: window.innerHeight })
    const q = placement.value
    if (p.top !== q.top || p.left !== q.left || p.side !== q.side) placement.value = p
  }
  frame = requestAnimationFrame(measure)
}

/** Go to the step's screen and wait (up to 6 s) for its element: screens load their data after they open. */
async function show() {
  const step = tour.step
  if (!step) return
  const mine = ++token
  target = null
  rect.value = null
  missing.value = false
  searching.value = step.targets.length > 0
  if (router.currentRoute.value.path !== step.route) await router.push(step.route).catch(() => undefined)
  await nextTick()
  const until = Date.now() + 6000
  while (step.targets.length && mine === token) {
    const el = findTarget(step.targets)
    if (el) {
      target = el
      el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' as ScrollBehavior })
      break
    }
    if (Date.now() > until) {
      missing.value = true
      break
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  if (mine !== token) return
  searching.value = false
  await nextTick()
  nextBtn.value?.focus({ preventScroll: true })
}

function onKey(e: KeyboardEvent) {
  if (!tour.active) return
  if (e.key === 'Escape') tour.stop()
  else if (e.key === 'Enter' && !(e.target instanceof HTMLButtonElement)) tour.next()
  // Right to left: the left arrow goes forward.
  else if (e.key === 'ArrowLeft') tour.next()
  else if (e.key === 'ArrowRight') tour.prev()
  else return
  e.preventDefault()
  e.stopPropagation()
}

watch(
  () => tour.index,
  (i, before) => {
    if (i === null) {
      cancelAnimationFrame(frame)
      window.removeEventListener('keydown', onKey, true)
      token++
      target = null
      return
    }
    if (before === null || before === undefined) {
      window.addEventListener('keydown', onKey, true)
      frame = requestAnimationFrame(measure)
    }
    void show()
  },
)

onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  window.removeEventListener('keydown', onKey, true)
})

const spotStyle = computed(() => {
  const r = rect.value
  return r ? { top: `${r.top}px`, left: `${r.left}px`, width: `${r.width}px`, height: `${r.height}px` } : {}
})
const bubbleStyle = computed(() => ({ top: `${placement.value.top}px`, left: `${placement.value.left}px` }))
</script>

<template>
  <div v-if="tour.active && tour.step" class="tour" data-testid="tour">
    <!-- Takes every click on the page while the tour is open. -->
    <div class="blocker" :class="{ dim: !rect }" @click.stop />
    <div v-if="rect" class="spot" :style="spotStyle" data-testid="tour-spotlight" aria-hidden="true" />

    <section
      ref="bubble"
      class="bubble"
      :class="`side-${placement.side}`"
      :style="bubbleStyle"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tour-title"
      dir="rtl"
      lang="he"
      data-testid="tour-bubble"
      :data-step="tour.step.id"
      :data-anchored="rect ? 'true' : 'false'"
    >
      <header>
        <span class="count" data-testid="tour-count">שלב {{ n }} מתוך {{ total }}</span>
        <button type="button" class="close" aria-label="סגירת הסיור" data-testid="tour-close" @click="tour.stop()">✕</button>
      </header>
      <div class="dots" aria-hidden="true">
        <span v-for="(s, i) in tour.steps" :key="s.id" :class="{ on: i === tour.index, past: i < (tour.index ?? 0) }" />
      </div>
      <h2 id="tour-title">{{ tour.step.title }}</h2>
      <p v-if="searching" class="muted">מחפש את המקום על המסך…</p>
      <p v-if="missing && tour.step.missing" class="missing" data-testid="tour-missing" v-html="richText(tour.step.missing)" />
      <!-- eslint-disable vue/no-v-html -- our own step texts, escaped by richText -->
      <p v-for="(line, i) in tour.step.body" :key="i" v-html="richText(line)" />
      <p v-if="tour.step.why" class="why"><strong>למה זה חשוב?</strong> <span v-html="richText(tour.step.why)" /></p>
      <!-- eslint-enable vue/no-v-html -->
      <footer>
        <button type="button" class="btn btn-primary" data-testid="tour-next" ref="nextBtn" @click="tour.next()">
          {{ tour.isLast ? 'סיום' : 'הבא' }}
        </button>
        <button v-if="!tour.isFirst" type="button" class="btn" data-testid="tour-prev" @click="tour.prev()">הקודם</button>
        <button v-if="!tour.isLast" type="button" class="btn skip" data-testid="tour-skip" @click="tour.stop()">דילוג על הסיור</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.tour {
  position: fixed;
  inset: 0;
  z-index: 100;
}
.blocker {
  position: absolute;
  inset: 0;
  background: transparent;
  transition: background 0.2s ease;
}
.blocker.dim {
  background: rgba(5, 7, 10, 0.72);
}
.spot {
  position: fixed;
  border-radius: 12px;
  box-shadow:
    0 0 0 2px var(--accent),
    0 0 0 6px rgba(76, 141, 255, 0.25),
    0 0 0 200vmax rgba(5, 7, 10, 0.72);
  pointer-events: none;
  transition:
    top 0.25s ease,
    left 0.25s ease,
    width 0.25s ease,
    height 0.25s ease;
}
.bubble {
  position: fixed;
  width: min(380px, calc(100vw - 24px));
  max-height: calc(100vh - 24px);
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.55);
  padding: 16px 18px 14px;
  font-size: 14px;
  line-height: 1.65;
  color: var(--text);
  text-align: start;
  animation: pop 0.2s ease;
}
@keyframes pop {
  from {
    opacity: 0;
    transform: translateY(6px) scale(0.98);
  }
}
@media (prefers-reduced-motion: reduce) {
  .bubble,
  .spot {
    animation: none;
    transition: none;
  }
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.count {
  font: 600 12px var(--font);
  color: var(--accent);
}
.close {
  background: none;
  border: 0;
  color: var(--muted);
  font-size: 15px;
  cursor: pointer;
  width: 32px;
  height: 32px;
  border-radius: 50%;
}
.close:hover {
  background: var(--surface-2);
  color: var(--text);
}
.dots {
  display: flex;
  gap: 5px;
  margin: 4px 0 8px;
}
.dots span {
  height: 4px;
  flex: 1;
  border-radius: 2px;
  background: var(--border);
}
.dots .past {
  background: rgba(76, 141, 255, 0.45);
}
.dots .on {
  background: var(--accent);
}
h2 {
  font: 700 17px var(--font-display);
  margin: 0 0 6px;
}
p {
  margin: 6px 0;
}
.bubble :deep(strong) {
  color: var(--text);
}
.why {
  margin-top: 10px;
  padding: 8px 10px;
  background: var(--accent-bg);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.missing {
  padding: 8px 10px;
  background: var(--warn-bg);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.muted {
  color: var(--muted);
  font-size: 13px;
}
footer {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}
.skip {
  margin-inline-start: auto;
  background: none;
  border-color: transparent;
  color: var(--muted);
}
</style>
