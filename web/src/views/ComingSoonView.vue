<script setup lang="ts">
// Screens from the design that later roadmap phases build. They say what they will hold instead of showing
// controls that do nothing.
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import Icon from '@/components/ui/Icon.vue'

const route = useRoute()
const meta = computed(() => route.meta)
</script>

<template>
  <div class="page-head">
    <div>
      <h1>{{ meta.title }}</h1>
      <p>Arrives in phase {{ meta.phase }} of the roadmap</p>
    </div>
  </div>
  <section class="panel soon">
    <span class="chip accent">Phase {{ meta.phase }}</span>
    <p class="blurb">{{ meta.blurb }}</p>
    <div class="links">
      <RouterLink to="/live" class="btn"><Icon name="live" :size="14" />Live floor</RouterLink>
      <RouterLink to="/system" class="btn"><Icon name="gear" :size="14" />System &amp; Agents</RouterLink>
    </div>
  </section>
</template>

<style scoped>
.soon {
  align-items: flex-start;
  max-width: 640px;
}
.blurb {
  font-size: 14px;
  line-height: 1.6;
}
.links {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
