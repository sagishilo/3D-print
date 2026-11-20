<script setup lang="ts">
// Catalog > Assign niche: move several products to one main niche at once (catalog.niche.assign). Orders already
// sold keep the niche they were sold under.
import { computed, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import type { CatalogProduct } from '@/lib/catalog'
import { nicheColor } from '@/lib/niches'
import { useCatalog } from '@/stores/catalog'
import { useNiche } from '@/stores/niche'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ products: CatalogProduct[] }>()
const emit = defineEmits<{ close: [] }>()
const niche = useNiche()
const catalog = useCatalog()
const toasts = useToasts()

const picked = ref<string[]>([])
const target = ref(niche.current ?? niche.list[0]?.slug ?? '')
const busy = ref(false)
const error = ref('')
const all = computed({
  get: () => props.products.length > 0 && picked.value.length === props.products.length,
  set: (v: boolean) => (picked.value = v ? props.products.map((p) => p.sku) : []),
})

async function submit() {
  if (!picked.value.length || !target.value || busy.value) return
  busy.value = true
  try {
    await niche.assign(picked.value, target.value)
    toasts.info(`Moving ${picked.value.length} product${picked.value.length === 1 ? '' : 's'} to ${niche.name(target.value)}`,
      'The catalog confirms on the live feed.')
    catalog.reloadSoon()
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="assign-title" data-testid="assign-niche-dialog" @submit.prevent="submit">
      <header>
        <h2 id="assign-title">Assign niche</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </header>
      <label class="target">
        Move to
        <select v-model="target" class="select" data-testid="assign-target">
          <option v-for="n in niche.list" :key="n.slug" :value="n.slug">{{ n.name }}</option>
        </select>
      </label>
      <label class="check all"><input v-model="all" type="checkbox" data-testid="assign-all" /> All {{ products.length }} shown</label>
      <ul class="list">
        <li v-for="p in products" :key="p.sku">
          <label class="check">
            <input v-model="picked" type="checkbox" :value="p.sku" :data-testid="`assign-${p.sku}`" />
            <span class="mono">{{ p.sku }}</span> {{ p.name }}
            <span class="now"><span class="dot" :style="{ background: nicheColor(niche.bySlug[p.niche]) }" />{{ niche.name(p.niche) }}</span>
          </label>
        </li>
      </ul>
      <p class="muted small">Orders already sold keep the niche they were sold under.</p>
      <p v-if="error" class="err" role="alert">{{ error }}</p>
      <footer>
        <button type="button" class="btn" @click="emit('close')">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!picked.length || !target || busy" data-testid="assign-save">
          Move {{ picked.length || '' }}
        </button>
      </footer>
    </form>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  z-index: 45;
  display: grid;
  place-items: center;
  padding: 16px;
}
.dialog {
  width: min(560px, 100%);
  max-height: calc(100vh - 32px);
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}
header,
footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
footer {
  justify-content: flex-end;
}
h2 {
  margin: 0;
  font-size: 16px;
}
.target {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.list li {
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}
.list li:last-child {
  border-bottom: 0;
}
.now {
  margin-inline-start: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.err {
  color: var(--danger-text);
}
</style>
