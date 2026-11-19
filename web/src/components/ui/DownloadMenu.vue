<script setup lang="ts">
// "Download" for any report: CSV (opens in Excel with Hebrew intact) or Excel (.xlsx). Plain links: the session
// cookie authorizes the file, and the browser saves it.
import Icon from '@/components/ui/Icon.vue'
import { download } from '@/lib/analytics'

defineProps<{ path: string; label?: string; testid?: string }>()
</script>

<template>
  <span class="downloads" :data-testid="testid">
    <Icon name="download" :size="14" />
    <span class="muted small">{{ label ?? 'Download' }}</span>
    <a class="btn btn-sm" :href="download(path, 'csv')" download :data-testid="testid ? `${testid}-csv` : undefined">CSV</a>
    <a class="btn btn-sm" :href="download(path, 'xlsx')" download :data-testid="testid ? `${testid}-xlsx` : undefined">Excel</a>
  </span>
</template>

<style scoped>
.downloads {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
</style>
