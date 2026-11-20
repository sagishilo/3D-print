<script setup lang="ts">
// Inventory > Filament: filament condition. Open spools take on moisture: each material has a number of days after
// which it should be dried (PETG, TPU and nylon are short). Mark dried restarts the clock. An AMS reporting a high
// humidity index needs fresh desiccant.
import { computed } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { vCan } from '@/directives/can'
import { errorText } from '@/lib/api'
import { useStock } from '@/stores/stock'
import { useToasts } from '@/stores/toasts'

const stock = useStock()
const toasts = useToasts()
const due = computed(() => (stock.condition?.spools ?? []).filter((s) => s.needs_drying))
const humid = computed(() => stock.condition?.humid ?? [])

async function dried(spoolId: string) {
  try {
    await stock.markDried(spoolId)
    toasts.info(`${spoolId} marked dried`, 'The drying clock starts again.')
    stock.reloadSoon()
  } catch (err) {
    toasts.error('Not saved', errorText(err))
  }
}
</script>

<template>
  <section class="panel" data-testid="condition" data-tour="condition">
    <div class="panel-head">
      <h2>Filament condition</h2>
      <span class="panel-note">days open against each material's limit · AMS humidity from the printers</span>
    </div>
    <p v-if="!due.length && !humid.length" class="ok-line" data-testid="condition-ok"><Icon name="check" :size="14" /> Every open spool is within its drying limit, and no AMS reports high humidity.</p>
    <ul v-if="humid.length" class="rows" data-testid="humid">
      <li v-for="h in humid" :key="`${h.printer_id}-${h.unit}`">
        <Icon name="alert" :size="14" class="warn" />
        <span><b>{{ h.printer_id }}</b> AMS {{ h.unit + 1 }}: humidity index {{ h.level }} of 5. Replace or dry the desiccant, and keep the lid closed.</span>
      </li>
    </ul>
    <ul v-if="due.length" class="rows" data-testid="drying-due">
      <li v-for="s in due" :key="s.spool_id" :data-testid="`dry-${s.spool_id}`">
        <Icon name="alert" :size="14" class="warn" />
        <span><b class="mono">{{ s.spool_id }}</b> {{ s.material }} {{ s.color }} · open {{ Math.floor(s.days_open ?? 0) }} days (dry after {{ s.dry_after_days }})<template v-if="s.printer_id"> · on {{ s.printer_id }}</template></span>
        <button v-can="'operator'" type="button" class="btn btn-sm" :data-testid="`mark-dried-${s.spool_id}`" @click="dried(s.spool_id)">Mark dried</button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rows li {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  flex-wrap: wrap;
}
.rows li button {
  margin-inline-start: auto;
}
.warn {
  color: var(--warn);
}
.ok-line {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  color: var(--muted);
  margin: 0;
}
</style>
