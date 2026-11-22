<script setup lang="ts">
// Import 3MF (a new SKU, or a new template for an existing one) and Edit. The file goes to the gateway first, which
// says what it holds (sliced? time and weight from the slicer, footprint); the personalization agent then checks it
// again and keeps it. Grams and minutes are typed in only for an unsliced model. The product image is the 3MF's own
// preview, or a picture uploaded here (kept when a new 3MF comes); the owner can delete a product nothing refers to.
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import Icon from '@/components/ui/Icon.vue'
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue'
import { errorText } from '@/lib/api'
import {
  type CatalogProduct,
  CATEGORY_LABEL,
  IMAGE_TYPES,
  type ImageUpload,
  licenceStatus,
  type LinkInfo,
  MAX_IMAGE_BYTES,
  minutes,
  skuError,
  skuFromName,
  type Upload,
} from '@/lib/catalog'
import { useProductActions } from '@/lib/productActions'
import { useAuth } from '@/stores/auth'
import { bytes, shekels } from '@/lib/format'
import { UNASSIGNED } from '@/lib/niches'
import { toAgorot } from '@/lib/orders'
import { useCatalog } from '@/stores/catalog'
import { useNiche } from '@/stores/niche'
import { useStock } from '@/stores/stock'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ product: CatalogProduct | null; categories: string[]; linkFirst?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const catalog = useCatalog()
const toasts = useToasts()
const niche = useNiche()
const auth = useAuth()
const actions = useProductActions()
const { t } = useI18n()

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
  post: p?.post_minutes ? String(p.post_minutes) : '',
  niche: p?.niche ?? niche.current ?? UNASSIGNED,
  tags: [...(p?.tags ?? [])] as string[],
  link: p?.source_url ?? '',
  designer: p?.designer ?? '',
  licence: p?.licence ?? '',
})

// Import from link (contracts/sources.py): the page is recognised and kept; only a site with a public API whose
// connector is on (Thingiverse) gives details. MakerWorld / Printables: the details come from the downloaded 3MF.
const linkInfo = ref<LinkInfo | null>(null)
const linkBusy = ref(false)
const linkError = ref('')
const linkInput = ref<HTMLInputElement | null>(null)
async function checkLink() {
  linkError.value = ''
  linkInfo.value = null
  if (!form.link.trim()) return
  linkBusy.value = true
  try {
    linkInfo.value = await catalog.link(form.link.trim())
    form.link = linkInfo.value.url
  } catch (err) {
    linkError.value = errorText(err)
  } finally {
    linkBusy.value = false
  }
}
function useLookup() {
  const l = linkInfo.value?.lookup
  if (!l) return
  if (!form.name.trim() && l.title) form.name = l.title.slice(0, 80)
  if (l.designer) form.designer = l.designer
  if (l.licence) form.licence = l.licence
}
const newOff = ref(false)
const licence = computed(() => licenceStatus(form.licence))
const brands = computed(() => [...new Set([...(p?.trademarks ?? []), ...(upload.value?.info.design?.trademarks ?? []),
  ...(linkInfo.value?.lookup?.trademarks ?? [])])])
// Going on sale under a licence that does not allow selling prints: the owner's word that they hold the rights.
const needsRights = computed(() => licence.value === 'non_commercial' && form.active && !(p?.active))
const rightsChecked = ref(false)
// The main niche (archived ones only when the product is still in one) and the extra niches it is also tagged with.
const nicheChoices = computed(() => niche.all.filter((n) => !n.archived || n.slug === form.niche))
const tagChoices = computed(() => niche.list.filter((n) => n.slug !== form.niche && n.slug !== UNASSIGNED))
/** A new product starts with its niche's material and first colour. */
function nicheDefaults() {
  const n = niche.bySlug[form.niche]
  if (p || !n) return
  form.material = n.default_material
  if (n.default_colors[0] && COLORS.includes(n.default_colors[0])) form.color = n.default_colors[0]
}
if (!niche.loaded) void niche.load()
if (!p) nicheDefaults()

// Parts per piece (the bill of materials, Wave 1 / M4): magnets, inserts, boxes… kept by the inventory agent.
const stock = useStock()
const bomRows = ref<{ item_id: string; qty: string }[]>([])
let bomBefore = '[]'
const bomReady = ref(!p)            // a product's saved list is loaded before it can be changed
if (!stock.loaded) void stock.load()
if (p) {
  void stock.bom(p.sku).then((rows) => {
    bomRows.value = rows.map((r) => ({ item_id: r.item_id, qty: String(r.qty) }))
    bomBefore = JSON.stringify(rows.map((r) => ({ item_id: r.item_id, qty: Number(r.qty) })))
    bomReady.value = true
  }).catch(() => (bomReady.value = true))
}
const bomPiece = computed(() =>
  bomRows.value.reduce((a, r) => a + Number(r.qty || 0) * (stock.items.find((i) => i.item_id === r.item_id)?.cost_agorot ?? 0), 0))

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
    const design = upload.value.info.design
    if (design) {
      if (!form.designer) form.designer = design.designer
      if (!form.licence) form.licence = design.licence
      if (!p && design.title && (!form.name || form.name === file.name.replace(/\.(gcode\.)?3mf$/i, '').slice(0, 80)))
        form.name = design.title.slice(0, 80)
      if (!p && licenceStatus(form.licence) === 'non_commercial') {
        form.active = false
        newOff.value = true
      }
    }
  } catch (err) {
    error.value = errorText(err)
  } finally {
    uploading.value = false
  }
}

// The product picture: a new one picked here is uploaded at once and saved with the product.
const image = ref<ImageUpload | null>(null)
const imagePreview = ref<string | null>(null)
const imageBusy = ref(false)
const imageError = ref('')
const shownImage = computed(() => imagePreview.value ?? p?.image_url ?? null)

async function pickImage(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  imageError.value = ''
  if (!file) return
  if (!IMAGE_TYPES.includes(file.type)) {
    imageError.value = t('catalog.imageTypes')
    input.value = ''
    return
  }
  if (file.size > MAX_IMAGE_BYTES) {
    imageError.value = t('catalog.imageTypes')
    input.value = ''
    return
  }
  imageBusy.value = true
  try {
    image.value = await catalog.uploadImage(file)
    if (imagePreview.value) URL.revokeObjectURL(imagePreview.value)
    imagePreview.value = URL.createObjectURL(file)
  } catch (err) {
    imageError.value = errorText(err)
  } finally {
    imageBusy.value = false
  }
}
onBeforeUnmount(() => {
  if (imagePreview.value) URL.revokeObjectURL(imagePreview.value)
})

async function removeProduct() {
  if (p && (await actions.remove(p))) emit('close')
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
  if (form.post !== '' && !(Number(form.post) >= 0 && Number(form.post) <= 600)) return 'finishing minutes: 0 to 600'
  if (upload.value && upload.value.info.plates > 1) return 'the file holds several sliced plates: export one plate per file'
  if (!fromSlicer.value && !(Number(form.grams) > 0 && Number(form.minutes) > 0)) return 'enter the grams and print minutes (the template is not sliced)'
  if (needsRights.value && !rightsChecked.value) return t('catalog.rightsConfirm')
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
      niche: form.niche,
      post_minutes: form.post === '' ? 0 : Number(form.post),
      tags: form.tags.filter((t) => t !== form.niche),
      image_upload: image.value?.image_upload ?? '',
      source_url: form.link.trim(),
      designer: form.designer.trim(),
      licence: form.licence.trim(),
      rights_confirmed: needsRights.value && rightsChecked.value,
    })
    const lines = bomRows.value.filter((r) => r.item_id && Number(r.qty) > 0).map((r) => ({ item_id: r.item_id, qty: Number(r.qty) }))
    if (JSON.stringify(lines) !== bomBefore) await stock.setBom(form.sku, lines)
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
  if (props.linkFirst) linkInput.value?.focus()
  else if (!p) fileInput.value?.focus()
})
</script>

<template>
  <div class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="product-title" data-testid="product-dialog" @submit.prevent="submit">
      <header>
        <h2 id="product-title">{{ p ? `Edit ${p.sku}` : 'Import 3MF' }}</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </header>

      <section class="link" data-testid="product-link">
        <label>
          {{ t('catalog.link') }}
          <span class="link-row">
            <input ref="linkInput" v-model.trim="form.link" class="input" type="url" inputmode="url" dir="ltr" maxlength="500"
              :placeholder="t('catalog.linkPlaceholder')" data-testid="link-url" @keydown.enter.prevent="checkLink" />
            <button type="button" class="btn btn-sm" :disabled="!form.link || linkBusy" data-testid="link-check" @click="checkLink">{{ t('catalog.linkCheck') }}</button>
          </span>
        </label>
        <span v-if="linkBusy" class="muted small">{{ t('catalog.linkChecking') }}</span>
        <span v-else-if="linkError" class="err small" role="alert" data-testid="link-error">{{ linkError }}</span>
        <div v-else-if="linkInfo" class="info" data-testid="link-info">
          <span>
            <b>{{ t('catalog.linkModel', { site: linkInfo.site_name, id: linkInfo.model_id }) }}</b> ·
            <a :href="linkInfo.url" target="_blank" rel="noopener noreferrer" data-testid="link-open">{{ t('catalog.linkOpen', { site: linkInfo.site_name }) }} ↗</a>
          </span>
          <template v-if="linkInfo.lookup">
            <span>{{ t('catalog.linkFound', { site: linkInfo.site_name }) }} <b>{{ linkInfo.lookup.title }}</b> · {{ linkInfo.lookup.designer }} · {{ linkInfo.lookup.licence }}
              <span v-if="linkInfo.lookup.mode === 'fake'" class="muted">({{ t('catalog.linkFake') }})</span></span>
            <span class="muted small">{{ linkInfo.lookup.has_3mf ? t('catalog.linkHas3mf') : t('catalog.linkNo3mf') }}</span>
            <button type="button" class="btn btn-sm use" data-testid="link-use" @click="useLookup">{{ t('catalog.linkUse') }}</button>
          </template>
          <span v-else-if="linkInfo.site !== 'thingiverse'" class="muted small" data-testid="link-note">{{ t('catalog.linkNoApi', { site: linkInfo.site_name }) }}</span>
          <span v-else class="muted small" data-testid="link-note">{{ linkInfo.note }}</span>
        </div>
        <span v-else-if="p?.source_url && form.link === p.source_url" class="small">
          <a :href="p.source_url" target="_blank" rel="noopener noreferrer">{{ t('catalog.source') }} ↗</a>
        </span>
        <span v-else class="muted small">{{ t('catalog.linkHint') }}</span>
      </section>

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

      <section class="image" data-testid="product-image">
        <div class="pic">
          <img v-if="shownImage" :src="shownImage" :alt="t('catalog.imageAlt', { name: form.name || form.sku })" data-testid="product-image-preview" />
          <Icon v-else name="camera" :size="22" />
        </div>
        <div class="pic-side">
          <b>{{ t('catalog.image') }}</b>
          <span class="muted small" data-testid="product-image-source">
            {{ image ? t('catalog.imageReady') : p?.image_source === 'upload' ? t('catalog.imageUploaded') : p?.image_url ? t('catalog.imageFrom3mf') : t('catalog.imageNone') }}
          </span>
          <label class="pick">
            <span class="btn btn-sm"><Icon name="camera" :size="14" />{{ shownImage ? t('catalog.imageReplace') : t('catalog.imagePick') }}</span>
            <input class="sr-only" type="file" accept="image/png,image/jpeg,image/webp" data-testid="image-file" @change="pickImage" />
          </label>
          <span v-if="imageBusy" class="muted small">{{ t('catalog.imageUploading') }}</span>
          <span v-else-if="imageError" class="err small" role="alert">{{ imageError }}</span>
          <span v-else class="muted small">{{ t('catalog.imageTypes') }}</span>
        </div>
      </section>

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
        <label>
          Niche
          <select v-model="form.niche" class="select" data-testid="product-niche" @change="nicheDefaults">
            <option v-for="n in nicheChoices" :key="n.slug" :value="n.slug">{{ n.name }}</option>
          </select>
        </label>
        <fieldset v-if="tagChoices.length" class="tags">
          <legend>Also in <span class="muted">(extra niches, optional)</span></legend>
          <label v-for="n in tagChoices" :key="n.slug" class="check"><input v-model="form.tags" type="checkbox" :value="n.slug" :data-testid="`product-tag-${n.slug}`" /> {{ n.name }}</label>
        </fieldset>
        <label>{{ t('catalog.designer') }}<input v-model="form.designer" class="input" maxlength="120" data-testid="designer" /></label>
        <label>
          {{ t('catalog.licence') }}
          <input v-model="form.licence" class="input" maxlength="120" data-testid="licence" />
        </label>
        <p class="verdict" :class="licence" data-testid="licence-verdict">
          {{ licence === 'commercial' ? t('catalog.licenceOk') : licence === 'non_commercial' ? t('catalog.licenceNo') : t('catalog.licenceUnknown') }}
          <span v-if="upload?.info.design" class="muted"> · {{ t('catalog.licenceFrom3mf', { designer: upload.info.design.designer, licence: upload.info.design.licence }) }}</span>
        </p>
        <p v-if="brands.length" class="verdict non_commercial" data-testid="brand-warning">™ {{ t('catalog.brandWarn', { terms: brands.join(', ') }) }}</p>
        <label>Finishing minutes per piece <span class="muted">(sanding, assembly: priced as labour)</span><input v-model="form.post" class="input" inputmode="decimal" placeholder="0" data-testid="post-minutes" /></label>
        <div class="sale-row" data-testid="product-sale">
          <ToggleSwitch v-model="form.active" :label="form.active ? t('catalog.switchOffLabel', { sku: form.sku }) : t('catalog.switchOnLabel', { sku: form.sku })" data-testid="product-active" />
          <span><b>{{ form.active ? t('catalog.onSale') : t('catalog.off') }}</b> <span class="muted">{{ newOff && !form.active ? t('catalog.newOffNote') : t('catalog.saleHelp') }}</span></span>
        </div>
        <label v-if="needsRights" class="check rights" data-testid="rights">
          <input v-model="rightsChecked" type="checkbox" data-testid="rights-confirm" /> {{ t('catalog.rightsConfirm') }}
        </label>
      </div>

      <fieldset class="bom" data-testid="bom">
        <legend>Parts per piece <span class="muted">(besides filament: used when made or shipped · Inventory › Parts &amp; packaging)</span></legend>
        <div v-for="(r, i) in bomRows" :key="i" class="bom-row">
          <select v-model="r.item_id" class="select" :aria-label="`Item ${i + 1}`" :data-testid="`bom-item-${i}`">
            <option value="" disabled>Choose an item</option>
            <option v-for="it in stock.items" :key="it.item_id" :value="it.item_id">{{ it.name }} ({{ it.unit }})</option>
          </select>
          <input v-model="r.qty" class="input qty" inputmode="decimal" :aria-label="`Quantity of item ${i + 1}`" :data-testid="`bom-qty-${i}`" />
          <button type="button" class="icon-btn" :aria-label="`Remove item ${i + 1}`" @click="bomRows.splice(i, 1)"><Icon name="x" :size="14" /></button>
        </div>
        <div class="bom-foot">
          <button type="button" class="btn btn-sm" :disabled="!stock.items.length || !bomReady" data-testid="bom-add" @click="bomRows.push({ item_id: '', qty: '1' })"><Icon name="plus" :size="12" />Add part</button>
          <span v-if="!stock.items.length" class="muted">Add parts and packaging under Inventory first.</span>
          <span v-else-if="bomRows.length" class="muted">{{ shekels(Math.round(bomPiece)) }} per piece</span>
        </div>
      </fieldset>

      <p v-if="error || (invalid && (upload || p))" class="err" role="alert">{{ error || invalid }}</p>
      <footer>
        <button v-if="p && auth.can('owner')" type="button" class="btn btn-danger-ghost" :aria-label="t('catalog.deleteLabel', { sku: p.sku })" data-testid="delete-product" @click="removeProduct">
          <Icon name="trash" :size="14" />{{ t('catalog.delete') }}
        </button>
        <span class="grow" />
        <button type="button" class="btn" @click="emit('close')">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!!invalid || busy || uploading || imageBusy" data-testid="save-product">
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
.tags {
  grid-column: 1 / -1;
  border: 0;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  font-size: 12px;
  color: var(--muted);
}
.tags legend {
  width: 100%;
  margin-bottom: 4px;
}
.tags label.check {
  grid-column: auto;
  color: var(--text);
}
.bom {
  border: 0;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.bom-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.bom-row .select {
  flex: 1;
}
.bom-row .qty {
  width: 80px;
}
.bom-foot {
  display: flex;
  gap: 10px;
  align-items: center;
}
.file {
  padding-top: 8px;
}
.link {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.link-row {
  display: flex;
  gap: 6px;
}
.link-row .input {
  flex: 1;
  min-width: 0;
}
.info .use {
  align-self: flex-start;
  margin-top: 4px;
}
.verdict {
  grid-column: 1 / -1;
  font-size: 12px;
  margin: 0;
}
.verdict.commercial {
  color: var(--ok);
}
.verdict.non_commercial {
  color: var(--warn);
}
.verdict.unknown {
  color: var(--muted);
}
label.check.rights {
  color: var(--text);
  align-items: flex-start;
}
.image {
  display: flex;
  gap: 12px;
  align-items: center;
}
.pic {
  width: 96px;
  height: 96px;
  flex: none;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2, var(--bg));
  color: var(--muted);
  overflow: hidden;
}
.pic img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.pic-side {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.pick {
  flex-direction: row;
  align-self: flex-start;
  cursor: pointer;
}
.pick:focus-within .btn {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.small {
  font-size: 12px;
}
.sale-row {
  grid-column: 1 / -1;
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
}
.grow {
  flex: 1;
}
.btn-danger-ghost {
  color: var(--danger);
  border-color: var(--danger);
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
