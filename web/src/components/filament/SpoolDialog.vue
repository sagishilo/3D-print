<script setup lang="ts">
// Register a spool, or work on one: load it on a printer (with its slot), put it back on a shelf, weigh it, mark it
// empty. Every action is a command; the inventory agent books it in the ledger and re-checks alerts and the rule.
import { computed, reactive, ref } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import { type Spool, where } from '@/lib/filament'
import { useInventory } from '@/stores/inventory'
import { usePrinters } from '@/stores/printers'
import { useToasts } from '@/stores/toasts'
import { vCan } from '@/directives/can'

const props = defineProps<{ spool: Spool | null; tag?: string }>()
const emit = defineEmits<{ close: [] }>()

const inventory = useInventory()
const printers = usePrinters()
const toasts = useToasts()
const busy = ref(false)
const error = ref('')

const reg = reactive({ material: 'PLA', color: 'black', brand: '', initial: '1000', remaining: '', location: '', tag: props.tag ?? '' })
const act = reactive({ printer: props.spool?.printer_id ?? '', slot: props.spool?.slot ?? '', location: props.spool?.location ?? '', grams: '' })
const isNew = computed(() => !props.spool)

async function run(label: string, fn: () => Promise<unknown>) {
  busy.value = true
  error.value = ''
  try {
    await fn()
    toasts.info(label, 'The inventory agent books it and re-checks alerts.')
    inventory.reloadSoon()
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

function register() {
  const initial = Number(reg.initial)
  if (!(initial > 0) || !reg.material.trim() || !reg.color.trim()) return (error.value = 'material, color and a weight above 0 are needed')
  return run('Spool registered', () =>
    inventory.register({
      material: reg.material.trim(), color: reg.color.trim(), brand: reg.brand.trim(), initial_grams: initial,
      ...(reg.remaining !== '' ? { remaining_grams: Number(reg.remaining) } : {}), location: reg.location.trim(), tag: reg.tag.trim(),
    }),
  )
}
const s = () => props.spool as Spool
const load = () => run(`Spool loaded on ${act.printer}`, () => inventory.loadSpool(s().spool_id, act.printer, act.slot.trim()))
const unload = () => run('Spool back on the shelf', () => inventory.loadSpool(s().spool_id, null, '', act.location.trim()))
function weigh() {
  const g = Number(act.grams)
  if (act.grams === '' || !(g >= 0)) return (error.value = 'enter the net weight in grams (without the empty spool)')
  return run('Spool weighed', () => inventory.adjust(s().spool_id, g))
}
const markEmpty = () => run('Spool marked empty', () => inventory.adjust(s().spool_id, 0, 'empty'))
</script>

<template>
  <div class="scrim" @click.self="emit('close')" @keydown.esc="emit('close')">
    <section class="dialog" role="dialog" aria-modal="true" :aria-label="isNew ? 'Register a spool' : `Spool ${spool?.spool_id}`" data-testid="spool-dialog">
      <header>
        <h2>{{ isNew ? 'Register a spool' : `${spool!.material} ${spool!.color} · ${spool!.spool_id}` }}</h2>
        <button type="button" class="icon-btn" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
      </header>

      <form v-if="isNew" class="grid" @submit.prevent="register">
        <label>Material<input v-model="reg.material" class="input" maxlength="20" required /></label>
        <label>Color<input v-model="reg.color" class="input" maxlength="30" required data-testid="reg-color" /></label>
        <label>Brand<input v-model="reg.brand" class="input" maxlength="40" /></label>
        <label>Spool weight (g)<input v-model="reg.initial" class="input" inputmode="numeric" /></label>
        <label>Left now (g, empty = full)<input v-model="reg.remaining" class="input" inputmode="numeric" /></label>
        <label>Shelf<input v-model="reg.location" class="input" maxlength="40" placeholder="B2" /></label>
        <label class="wide">Label / RFID (becomes the spool id)<input v-model="reg.tag" class="input" maxlength="80" /></label>
        <button v-can="'operator'" type="submit" class="btn btn-primary wide" :disabled="busy">Register spool</button>
      </form>

      <template v-else>
        <p class="muted">
          {{ Math.round(spool!.remaining_grams) }} of {{ Math.round(spool!.initial_grams) }} g · {{ where(spool!) }}
          · {{ spool!.brand || 'unknown brand' }}{{ spool!.tag ? ` · label ${spool!.tag}` : '' }}
          · weighed {{ spool!.weighed_at ? dateTime(spool!.weighed_at) : 'never' }}
        </p>
        <div class="block">
          <h3>Load on a printer</h3>
          <div class="row">
            <label>
              Printer
              <select v-model="act.printer" class="select" data-testid="load-printer">
                <option value="" disabled>choose</option>
                <option v-for="p in printers.list" :key="p.id" :value="p.id">{{ p.id }} ({{ p.material }} {{ p.color }})</option>
              </select>
            </label>
            <label>Slot<input v-model="act.slot" class="input" maxlength="40" placeholder="AMS 1 · slot 2" /></label>
            <button v-can="'operator'" type="button" class="btn" :disabled="busy || !act.printer || spool!.status === 'empty'" @click="load">Load</button>
          </div>
        </div>
        <div v-if="spool!.printer_id" class="block">
          <h3>Back on the shelf</h3>
          <div class="row">
            <label>Shelf<input v-model="act.location" class="input" maxlength="40" placeholder="B2" /></label>
            <button v-can="'operator'" type="button" class="btn" :disabled="busy" @click="unload">Unload</button>
          </div>
        </div>
        <div class="block">
          <h3>Weigh</h3>
          <div class="row">
            <label>Net weight (g)<input v-model="act.grams" class="input" inputmode="numeric" data-testid="weigh-grams" /></label>
            <button v-can="'operator'" type="button" class="btn" :disabled="busy" @click="weigh">Save weight</button>
            <button v-can="'operator'" type="button" class="btn btn-danger" :disabled="busy || spool!.status === 'empty'" @click="markEmpty">Mark empty</button>
          </div>
        </div>
      </template>
      <p v-if="error" class="err" role="alert">{{ error }}</p>
    </section>
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
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
h2 {
  font: 700 17px var(--font-display);
}
h3 {
  font: 600 13px var(--font);
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.wide {
  grid-column: span 2;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.block {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.err {
  color: var(--danger);
  font-size: 13px;
}
</style>
