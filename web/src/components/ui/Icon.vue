<script setup lang="ts">
// Line icons from the design (24-unit grid, 1.75 stroke). Decorative: always aria-hidden; label the control instead.
import { computed } from 'vue'

const props = withDefaults(defineProps<{ name: string; size?: number; strokeWidth?: number }>(), {
  size: 16,
  strokeWidth: 1.75,
})

const PATHS: Record<string, string[]> = {
  brand: ['M4 18h16M7 18V9l5-4 5 4v9'],
  activity: ['M3 12h4l3-8 4 16 3-8h4'],
  grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
  columns: ['M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z', 'M9 4v16M15 4v16'],
  monitor: ['M6 3h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', 'M8 21h8M12 15v6'],
  disc: ['M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z', 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z'],
  bars: ['M4 20V10M10 20V4M16 20v-7M22 20H2'],
  gear: [
    'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z',
    'M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1',
  ],
  live: [
    'M12 11.5a.5.5 0 1 0 0 1a.5.5 0 1 0 0-1z',
    'M8.5 15.5a5 5 0 0 1 0-7M15.5 8.5a5 5 0 0 1 0 7M5.6 18.4a9 9 0 0 1 0-12.8M18.4 5.6a9 9 0 0 1 0 12.8',
  ],
  search: ['M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z', 'M20 20l-4-4'],
  inbox: ['M3 13l3-8h12l3 8v6H3z', 'M3 13h5l1 3h6l1-3h5'],
  pause: ['M9 5v14M15 5v14'],
  play: ['M7 5l12 7-12 7z'],
  stop: ['M6 6h12v12H6z'],
  refresh: ['M20 11a8 8 0 1 0-2 6', 'M20 4v7h-7'],
  x: ['M6 6l12 12M18 6L6 18'],
  check: ['M5 12l5 5 9-10'],
  alert: ['M12 3l10 18H2z', 'M12 10v5M12 18v.5'],
  external: ['M14 4h6v6M20 4l-9 9M18 14v6H4V6h6'],
  plus: ['M12 5v14M5 12h14'],
  camera: ['M4 7h3l2-2h6l2 2h3v12H4z', 'M12 10a3 3 0 1 0 0 6a3 3 0 1 0 0-6z'],
  history: ['M3 12a9 9 0 1 0 3-6.7', 'M3 4v5h5', 'M12 7v5l3 2'],
  logout: ['M15 4h4v16h-4M10 17l5-5-5-5M15 12H3'],
  menu: ['M4 6h16M4 12h16M4 18h16'],
  download: ['M12 4v12M7 11l5 5 5-5M5 20h14'],
  copy: ['M9 9h10v11H9z', 'M5 15V4h10'],
  users: ['M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M2 21v-1a6 6 0 0 1 12 0v1', 'M16 3.5a4 4 0 0 1 0 7.5', 'M18 14a6 6 0 0 1 4 6v1'],
  lock: ['M6 11h12v10H6z', 'M8 11V7a4 4 0 0 1 8 0v4'],
  'chevron-down': ['M6 9l6 6 6-6'],
  tag: ['M3 12V4h8l10 10-8 8z', 'M7.5 7.5h.01'],
  calc: ['M6 3h12v18H6z', 'M9 7h6', 'M9 12h.01M12 12h.01M15 12h.01M9 16h.01M12 16h.01M15 16h.01'],
  clock: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M12 7v5l3 2'],
  wrench: ['M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z'],
  eye: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z', 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z'],
}

const paths = computed(() => PATHS[props.name] ?? PATHS.alert)
</script>

<template>
  <svg
    aria-hidden="true"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    style="flex: none"
  >
    <path v-for="(d, i) in paths" :key="i" :d="d" />
  </svg>
</template>
