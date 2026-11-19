<script setup lang="ts">
// Niches (Wave 1 / M3, owner): the product groups the business sells in. Each has a colour (the top-bar chip and
// every niche dot), the defaults new products start with (material, colours, packaging), a target margin (Finance
// flags a niche below it) and a QC checklist the order drawer shows before an order passes quality control.
import { computed, onMounted, reactive, ref } from 'vue'

import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { commaList, lines, type Niche, NICHE_COLORS, nicheColor, slugError, slugFromName, UNASSIGNED } from '@/lib/niches'
import { useConfirm } from '@/stores/confirm'
import { useNiche } from '@/stores/niche'
import { useToasts } from '@/stores/toasts'

const niche = useNiche()
const confirm = useConfirm()
const toasts = useToasts()

onMounted(() => void niche.load())

const showArchived = ref(false)
const rows = computed(() => niche.all.filter((n) => showArchived.value || !n.archived))

const editing = ref<Niche | null>(null)
const open = ref(false)
const busy = ref(false)
const error = ref('')
const form = reactive({ slug: '', name: '', color: 1, sort: 100, material: 'PLA', colors: '', margin: '50', checklist: '', packaging: '' })

function edit(n: Niche | null) {
  editing.value = n
  error.value = ''
  Object.assign(form, n
    ? { slug: n.slug, name: n.name, color: n.color, sort: n.sort, material: n.default_material, colors: n.default_colors.join(', '),
        margin: String(n.target_margin_pct), checklist: n.qc_checklist.join('\n'), packaging: n.packaging.join(', ') }
    : { slug: '', name: '', color: (niche.all.length % 7) + 1, sort: 100, material: 'PLA', colors: '', margin: '50', checklist: '', packaging: '' })
  open.value = true
}

function onName() {
  if (!editing.value) form.slug = slugFromName(form.name)
}

const invalid = computed(() => {
  if (!form.name.trim()) return 'the name is missing'
  if (!editing.value) {
    const e = slugError(form.slug)
    if (e) return e
    if (niche.bySlug[form.slug]) return 'a niche with this short name exists'
  }
  const m = Number(form.margin)
  if (!(m >= 0 && m < 100) || form.margin === '') return 'the target margin is a percentage from 0 to 99'
  if (lines(form.checklist).length > 20) return 'at most 20 checklist lines'
  return null
})

async function save() {
  if (invalid.value || busy.value) return
  busy.value = true
  try {
    await niche.upsert({
      slug: form.slug, name: form.name.trim(), color: form.color, sort: Number(form.sort) || 100,
      default_material: form.material.trim() || 'PLA', default_colors: commaList(form.colors),
      target_margin_pct: Number(form.margin), qc_checklist: lines(form.checklist), packaging: commaList(form.packaging),
    })
    toasts.info(`Saving ${form.name.trim()}`, 'The catalog confirms on the live feed.')
    open.value = false
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function archive(n: Niche) {
  if (n.archived) {
    await run(() => niche.archive(n.slug, false), `Restoring ${n.name}`)
    return
  }
  const ok = await confirm.ask({
    title: `Archive ${n.name}?`,
    text: `It disappears from the niche pickers. Its ${n.products} product${n.products === 1 ? '' : 's'} move to Unassigned; orders already sold keep their niche, so reports do not change.`,
    confirmLabel: 'Archive',
    danger: true,
  })
  if (ok) await run(() => niche.archive(n.slug, true, UNASSIGNED), `Archiving ${n.name}`)
}

async function run(fn: () => Promise<unknown>, what: string) {
  try {
    await fn()
    toasts.info(what, 'The catalog confirms on the live feed.')
  } catch (err) {
    toasts.error(`${what} failed`, errorText(err))
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Niches</h1>
      <p>The product groups you sell in · each with its colour, the defaults new products start with, a target margin and a QC checklist</p>
    </div>
    <button type="button" class="btn btn-primary" data-testid="add-niche" @click="edit(null)"><Icon name="plus" :size="14" />Add niche</button>
  </div>

  <div class="toolbar">
    <label class="small muted check"><input v-model="showArchived" type="checkbox" data-testid="show-archived" /> archived too</label>
  </div>
  <p v-if="niche.error" class="err" role="alert">{{ niche.error }}</p>

  <div class="grid" data-tour="niches-list">
    <article v-for="n in rows" :key="n.slug" class="card" :class="{ archived: n.archived }" :data-testid="`niche-${n.slug}`">
      <header>
        <span class="swatch" :style="{ background: nicheColor(n) }" />
        <h2>{{ n.name }}</h2>
        <span v-if="n.archived" class="pill">archived</span>
      </header>
      <dl>
        <div><dt>Products</dt><dd>{{ n.products }}<span v-if="n.tagged" class="muted"> + {{ n.tagged }} tagged</span></dd></div>
        <div><dt>Target margin</dt><dd>{{ n.target_margin_pct }}%</dd></div>
        <div><dt>Starts with</dt><dd>{{ n.default_material }}<template v-if="n.default_colors.length"> · {{ n.default_colors.join(', ') }}</template></dd></div>
        <div><dt>Packaging</dt><dd>{{ n.packaging.length ? n.packaging.join(', ') : '—' }}</dd></div>
      </dl>
      <div class="qc">
        <b class="small">QC checklist</b>
        <ul v-if="n.qc_checklist.length"><li v-for="c in n.qc_checklist" :key="c">{{ c }}</li></ul>
        <p v-else class="muted small">No checklist: QC is pass or fail only.</p>
      </div>
      <footer>
        <button type="button" class="btn btn-sm" :data-testid="`edit-niche-${n.slug}`" @click="edit(n)">Edit</button>
        <button v-if="n.slug !== UNASSIGNED" type="button" class="btn btn-sm" :data-testid="`archive-niche-${n.slug}`" @click="archive(n)">{{ n.archived ? 'Restore' : 'Archive' }}</button>
      </footer>
    </article>
  </div>
  <EmptyState v-if="niche.loaded && !rows.length && !niche.error" icon="tag" title="No niches yet"
    text="Add the groups you sell in (home décor, toys, spare parts…) to see revenue and margin per niche." action-label="Add niche" @action="edit(null)" />

  <div v-if="open" class="scrim" @click.self="open = false" @keydown.esc="open = false">
    <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="niche-title" data-testid="niche-dialog" @submit.prevent="save">
      <header>
        <h2 id="niche-title">{{ editing ? `Edit ${editing.name}` : 'Add niche' }}</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="open = false"><Icon name="x" /></button>
      </header>
      <div class="fields">
        <label>Name<input v-model="form.name" class="input" maxlength="40" data-testid="niche-name" @input="onName" /></label>
        <label>Short name<input v-model="form.slug" class="input mono" maxlength="32" :disabled="!!editing" data-testid="niche-slug" /></label>
        <fieldset class="colors">
          <legend>Colour</legend>
          <label v-for="c in NICHE_COLORS" :key="c" class="color" :style="{ background: `var(--niche-${c})` }" :class="{ on: form.color === c }">
            <input v-model="form.color" type="radio" name="niche-color" :value="c" class="sr-only" :data-testid="`niche-color-${c}`" />
            <span class="sr-only">Colour {{ c }}</span>
          </label>
        </fieldset>
        <label>Target margin %<input v-model="form.margin" class="input" inputmode="decimal" data-testid="niche-margin" /></label>
        <label>Default material<input v-model="form.material" class="input" maxlength="20" /></label>
        <label>Default colours <span class="muted small">(comma-separated)</span><input v-model="form.colors" class="input" placeholder="white, black" /></label>
        <label class="wide">Packaging <span class="muted small">(comma-separated; stock items arrive later)</span><input v-model="form.packaging" class="input" placeholder="Gift box S, sticker" /></label>
        <label class="wide">QC checklist <span class="muted small">(one check per line)</span>
          <textarea v-model="form.checklist" class="input" rows="5" data-testid="niche-checklist" placeholder="No sharp edges&#10;Colour matches the order" />
        </label>
        <label>Order in lists<input v-model="form.sort" class="input" inputmode="numeric" /></label>
      </div>
      <p v-if="invalid" class="muted small">{{ invalid }}</p>
      <p v-if="error" class="err" role="alert">{{ error }}</p>
      <footer>
        <button type="button" class="btn" @click="open = false">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!!invalid || busy" data-testid="save-niche">Save</button>
      </footer>
    </form>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--gap);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--gap);
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--pad-panel);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.card.archived {
  opacity: 0.7;
}
.card header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.card h2 {
  font-size: 15px;
  margin: 0;
}
.swatch {
  width: 14px;
  height: 14px;
  border-radius: 50%;
}
.pill {
  margin-left: auto;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--muted);
}
dl {
  margin: 0;
  display: grid;
  gap: 6px;
  font-size: 13px;
}
dl div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
dt {
  color: var(--muted);
}
dd {
  margin: 0;
  text-align: right;
}
.qc ul {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 13px;
}
.card footer {
  margin-top: auto;
  display: flex;
  gap: 8px;
}
.scrim {
  position: fixed;
  inset: 0;
  background: var(--scrim);
  display: grid;
  place-items: center;
  z-index: 50;
  padding: 16px;
}
.dialog {
  width: min(620px, 100%);
  max-height: calc(100vh - 32px);
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.dialog header,
.dialog footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.dialog footer {
  justify-content: flex-end;
}
.dialog h2 {
  margin: 0;
  font-size: 16px;
}
.fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.fields label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.fields .wide,
.colors {
  grid-column: 1 / -1;
}
.colors {
  border: 0;
  padding: 0;
  margin: 0;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.colors legend {
  font-size: 13px;
  margin-bottom: 4px;
}
.color {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid transparent;
}
.color.on {
  outline: 2px solid var(--text);
  outline-offset: 2px;
}
.color:focus-within {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.err {
  color: var(--danger-text);
}
@media (max-width: 560px) {
  .fields {
    grid-template-columns: 1fr;
  }
}
</style>
