<script setup lang="ts">
import type { Tone } from '@/lib/types'

defineProps<{ tone: Tone; label: string }>()
</script>

<template>
  <span class="pill" :class="`tone-${tone}`"><span class="dot" aria-hidden="true" />{{ label }}</span>
</template>
