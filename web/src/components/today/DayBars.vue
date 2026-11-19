<script setup lang="ts">
// A small single-series bar chart of the last days (one hue: the accent token; the title names the series, so no
// legend). Bars rise from the baseline with rounded data ends and a 2 px gap; hovering or focusing a bar shows its
// value; "Table" shows the same numbers as a table (never colour alone, never chart alone).
import { computed, ref } from 'vue'

import { dayLabel } from '@/lib/today'

const props = defineProps<{ title: string; points: { day: string; value: number }[]; format: (v: number) => string; testid?: string }>()

const H = 96 // plot height (viewBox units)
const W = 280
const max = computed(() => Math.max(1, ...props.points.map((p) => p.value)))
const step = computed(() => W / Math.max(props.points.length, 1))
const bars = computed(() => props.points.map((p, i) => {
  const h = p.value > 0 ? Math.max(3, (p.value / max.value) * H) : 0
  return { ...p, x: i * step.value + 1, w: step.value - 2, y: H - h, h, last: i === props.points.length - 1 }
}))
const total = computed(() => props.points.reduce((a, p) => a + p.value, 0))
const hover = ref<number | null>(null)
const table = ref(false)
</script>

<template>
  <figure class="chart" :data-testid="testid">
    <figcaption>
      <span class="t">{{ title }}</span>
      <b class="sum">{{ format(total) }}</b>
      <button type="button" class="link small" :aria-pressed="table" @click="table = !table">{{ table ? 'Chart' : 'Table' }}</button>
    </figcaption>
    <template v-if="!table">
      <div class="plot" @mouseleave="hover = null">
        <svg :viewBox="`0 0 ${W} ${H + 1}`" preserveAspectRatio="none" role="img" :aria-label="`${title}: ${points.map((p) => `${dayLabel(p.day)} ${format(p.value)}`).join(', ')}`">
          <line class="base" x1="0" :x2="W" :y1="H + 0.5" :y2="H + 0.5" />
          <g v-for="(b, i) in bars" :key="b.day">
            <rect class="hit" :x="b.x - 1" y="0" :width="b.w + 2" :height="H" @mouseenter="hover = i" />
            <path v-if="b.h" class="bar" :class="{ on: hover === i, last: b.last }"
              :d="`M${b.x},${H} V${b.y + Math.min(4, b.h)} q0,-4 4,-4 H${b.x + b.w - 4} q4,0 4,4 V${H} Z`" />
          </g>
        </svg>
        <div v-if="hover !== null" class="tip" :style="{ left: `${((hover + 0.5) / points.length) * 100}%` }" role="status">
          <span class="muted">{{ dayLabel(points[hover]!.day) }}</span> <b>{{ format(points[hover]!.value) }}</b>
        </div>
      </div>
      <div class="days" aria-hidden="true">
        <button v-for="(p, i) in points" :key="p.day" type="button" class="day" :class="{ on: hover === i }" tabindex="0"
          @focus="hover = i" @blur="hover = null" @mouseenter="hover = i">{{ dayLabel(p.day).split(' ')[0] }}</button>
      </div>
    </template>
    <table v-else class="data small">
      <tbody>
        <tr v-for="p in points" :key="p.day"><td>{{ dayLabel(p.day) }}</td><td class="num">{{ format(p.value) }}</td></tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.chart {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
figcaption {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
.sum {
  color: var(--text);
  font: 600 15px var(--font-display);
}
.link {
  margin-left: auto;
  background: none;
  border: 0;
  padding: 0;
  color: var(--accent);
  cursor: pointer;
  text-decoration: underline;
}
.plot {
  position: relative;
  height: 96px;
}
svg {
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
}
.base {
  stroke: var(--border-strong);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.hit {
  fill: transparent;
}
.bar {
  fill: var(--accent);
  opacity: 0.72;
}
.bar.last,
.bar.on {
  opacity: 1;
}
.tip {
  position: absolute;
  top: -6px;
  transform: translate(-50%, -100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  padding: 3px 8px;
  font-size: 12px;
  white-space: nowrap;
  pointer-events: none;
}
.days {
  display: flex;
}
.day {
  flex: 1;
  background: none;
  border: 0;
  padding: 0;
  font-size: 11px;
  color: var(--muted);
  cursor: default;
}
.day.on {
  color: var(--text);
}
</style>
