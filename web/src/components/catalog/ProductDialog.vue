<script setup lang="ts">
// Import 3MF (a new SKU, or a new template for an existing one) and Edit. The file goes to the gateway first, which
// says what it holds (sliced? time and weight from the slicer, footprint); the personalization agent then checks it
// again and keeps it. Grams and minutes are typed in only for an unsliced model.
import { computed, onMounted, reactive, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { type CatalogProduct, CATEGORY_LABEL, minutes, skuError, skuFromName, type Upload } from '@/lib/catalog'
import { bytes } from '@/lib/format'
import { toAgorot } from '@/lib/orders'
import { useCatalog } from '@/stores/catalog'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ product: CatalogProduct | null; categories: string[] }>()
const emit = defineEmits<{ close: [] }>()
const catalog = useCatalog()
const toasts = useToasts()

const COLORS = ['black', 'white', 'grey', 'red', 'blue', 'green', 'gold', 'clear']
const p = props.product
const form = reactive({
  sku: p?.sku ?? '',
  name: p?.name ?? '',
  category: p?.category ?? 'other',
  material: p?.material ?? 'PLA',
  color: p?.color ?? 'black',
  price: p ? (p.price_agorot / 100).toFixed(2) : '',
  grams: p?.estimated ? String(p.est_grams) : '',
  minutes: p?.estimated ? String(p.est_minutes) : '',
  active: p?.active ?? true,
})
const upload = ref<Upload | null>(null)
const uploading = ref(false)
const busy = ref(false)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

async function pick(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploading.value = true
  error.value = ''
  upload.value = null
  try {
    upload.value = await catalog.upload(file)
    if (!form.sku) form.sku = skuFromName(file.name)
    if (!form.name) form.name = file.name.replace(/\.(gcode\.)?3mf$/i, '').slice(0, 80)
    if (upload.value.info.filament && !p) form.material = upload.value.info.filament
  } catch (err) {
    error.value = errorText(err)
  } finally {
    uploading.value = false
  }
}

// Sliced and with the slicer's numbers (this upload, or the stored template when no new file is picked).
const fromSlicer = computed(() => {
  if (upload.value) return upload.value.info.sliced && upload.value.info.est_grams != null && upload.value.info.est_minutes != null
  return !!p && !p.estimated
})
const invalid = computed(() => {
  if (!p && !upload.value) return 'choose the 3MF template'
  const s = skuError(form.sku)
  if (s) return s
  if (!form.name.trim()) return 'the name is missing'
  if (!(Number(form.price) >= 0) || form.price === '') return 'enter the price'
  if (upload.value && upload.value.info.plates > 1) return 'the file holds several sliced plates: export one plate per file'
  if (!fromSlicer.value && !(Number(form.grams) > 0 && Number(form.minutes) > 0)) return 'enter the grams and print minutes (the template is not sliced)'
  return null
})

async function submit() {
  if (invalid.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await catalog.upsert({
      sku: form.sku,
      name: form.name.trim(),
      category: form.category,
      material: form.material.trim(),
      color: form.color,
      price_agorot: toAgorot(form.price),
      upload: upload.value?.upload ?? '',
      upload_name: upload.value?.name ?? '',
      est_grams: fromSlicer.value ? null : Number(form.grams),
      est_minutes: fromSlicer.value ? null : Number(form.minutes),
      active: form.active,
    })
    toasts.info(p ? `${form.sku} saved` : `${form.sku} imported`, 'The personalization agent checks the file and confirms on the live feed.')
    catalog.reloadSoon()
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (!p) fileInput.value?.focus()
})
</script>

<template>
  <div class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="product-title" data-testid="product-dialog" @submit.prevent="submit">
      <header>
        <h2 id="product-title">{{ p ? `Edit ${p.sku}` : 'Import 3MF' }}</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </header>

      <label>
        {{ p ? 'New template (optional)' : '3MF template' }}
        <input ref="fileInput" class="input file" type="file" accept=".3mf" data-testid="file" @change="pick" />
      </label>
      <p class="hint">Best: a plate <b>sliced</b> in Bambu Studio or OrcaSlicer (File › Export › Export plate sliced file): it prints as is, and its time and weight come from the slicer. A model-only 3MF works on the simulators; a real printer needs it sliced.</p>
      <p v-if="uploading" class="muted">Reading the file…</p>
      <div v-if="upload" class="info" data-testid="upload-info">
        <span><b>{{ upload.name }}</b> · {{ bytes(upload.bytes) }}</span>
        <span>
          {{ upload.info.sliced ? `sliced (${upload.info.plates} plate${upload.info.plates === 1 ? '' : 's'})` : 'not sliced' }}
          · {{ upload.info.objects }} object{{ upload.info.objects === 1 ? '' : 's' }}
          <template v-if="upload.info.footprint_mm"> · {{ upload.info.footprint_mm[0] }} × {{ upload.info.footprint_mm[1] }} mm</template>
        </span>
        <span v-if="upload.info.est_minutes != null">slicer: {{ minutes(upload.info.est_minutes) }} · {{ upload.info.est_grams }} g{{ upload.info.printer_model ? ` · printer ${upload.info.printer_model}` : '' }}</span>
        <span v-for="w in upload.info.warnings" :key="w" class="text-warn">{{ w }}</span>
      </div>

      <div class="grid">
        <label>SKU<input v-model="form.sku" class="input mono" maxlength="32" :disabled="!!p" data-testid="sku" @input="form.sku = form.sku.toUpperCase()" /></label>
        <label>Name<input v-model="form.name" class="input" maxlength="80" data-testid="name" /></label>
        <label>
          Category
          <select v-model="form.category" class="select">
            <option v-for="c in categories" :key="c" :value="c">{{ CATEGORY_LABEL[c] ?? c }}</option>
          </select>
        </label>
        <label>Price ₪ (incl. VAT)<input v-model="form.price" class="input" inputmode="decimal" data-testid="price" /></label>
        <label>Material<input v-model="form.material" class="input" maxlength="20" /></label>
        <label>
          Color
          <select v-model="form.color" class="select">
            <option v-for="c in COLORS" :key="c" :value="c">{{ c }}</option>
          </select>
        </label>
        <template v-if="!fromSlicer">
          <label>Filament g<input v-model="form.grams" class="input" inputmode="decimal" data-testid="grams" /></label>
          <label>Print minutes<input v-model="form.minutes" class="input" inputmode="decimal" data-testid="minutes" /></label>
        </template>
        <label v-if="p" class="check"><input v-model="form.active" type="checkbox" /> On sale (off: no new orders or batches)</label>
      </div>

      <p v-if="error || (invalid && (upload || p))" class="err" role="alert">{{ error || invalid }}</p>
      <footer>
        <button type="button" class="btn" @click="emit('close')">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!!invalid || busy || uploading" data-testid="save-product">
          {{ p ? 'Save' : 'Add to catalog' }}
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
  display: flex;
  align-items: flex-start;
  justify-content: center;
  overflow-y: auto;
  padding: 60px 16px;
}
.dialog {
  width: min(560px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
header,
footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
footer {
  justify-content: flex-end;
}
h2 {
  font: 700 17px var(--font-display);
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
label.check {
  flex-direction: row;
  align-items: center;
  grid-column: span 2;
}
.file {
  padding-top: 8px;
}
.hint {
  font-size: 12px;
  color: var(--muted);
}
.info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.err {
  color: var(--danger);
  font-size: 13px;
}
@media (max-width: 520px) {
  .grid {
    grid-template-columns: 1fr;
  }
  label.check {
    grid-column: auto;
  }
}
</style>
