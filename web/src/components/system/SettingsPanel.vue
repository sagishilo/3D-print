<script setup lang="ts">
// Automation toggles (NATS KV "config"). A change is a command (cmd.coordinator.set_config): the coordinator records
// who changed what, and agents pick the new value up within about 5 s. Settings for later phases are shown disabled.
import { computed, ref } from 'vue'

import ToggleSwitch from '@/components/ui/ToggleSwitch.vue'
import { errorText } from '@/lib/api'
import { dateTime } from '@/lib/format'
import type { Setting } from '@/lib/types'
import { needs } from '@/lib/roles'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { useSystem } from '@/stores/system'
import { useToasts } from '@/stores/toasts'

const auth = useAuth()

const system = useSystem()
const confirm = useConfirm()
const toasts = useToasts()
const saving = ref<string | null>(null)
// On/off settings only; number settings (thresholds, budget) are on the screens they tune (Social & Trends).
const switches = computed(() => system.settings.filter((s) => typeof s.default === 'boolean'))

const lastChange = computed(() => {
  const changed = system.settings.filter((s) => s.changed_at).sort((a, b) => (a.changed_at! < b.changed_at! ? 1 : -1))
  const s = changed[0]
  if (!s) return ''
  return `Last change: ${s.label} ${s.changed_value ? 'enabled' : 'disabled'} by ${s.changed_by ?? 'unknown'} · ${dateTime(s.changed_at)}`
})

async function change(s: Setting, value: boolean) {
  if (s.key === 'safety.auto_pause' && !value) {
    const ok = await confirm.ask({
      title: 'Turn off auto-pause?',
      text: 'Printers keep printing through a critical finding (thermal runaway, heating failure). Findings are still reported in the feed.',
      confirmLabel: 'Turn off',
      danger: true,
    })
    if (!ok) return
  }
  saving.value = s.key
  try {
    await system.setSetting(s.key, value)
    toasts.ok(`${s.label} ${value ? 'on' : 'off'}`, 'Saved and audited. Agents apply it within about 5 s.')
  } catch (err) {
    toasts.error('Could not change the setting', errorText(err))
  } finally {
    saving.value = null
  }
}
</script>

<template>
  <section class="panel" data-testid="settings">
    <div class="panel-head">
      <h2>Automation toggles</h2>
      <span class="panel-note">changes are audited</span>
    </div>
    <ul class="settings">
      <li v-for="s in switches" :key="s.key" :data-testid="`setting-${s.key}`">
        <div class="text">
          <span class="label">
            {{ s.label }}
            <span v-if="s.phase" class="chip" :title="`Arrives in phase ${s.phase}`">P{{ s.phase }}</span>
          </span>
          <span class="help">{{ s.help }}</span>
        </div>
        <ToggleSwitch
          :model-value="Boolean(s.value)"
          :label="s.label"
          :disabled="s.phase != null || saving === s.key || !auth.can('owner')"
          :title="auth.can('owner') ? undefined : needs('owner')"
          @update:model-value="(v: boolean) => change(s, v)"
        />
      </li>
    </ul>
    <p v-if="!system.settings.length" class="empty">Loading…</p>
    <p class="panel-note">There is no auto-post setting: every social reply is copied and posted by hand.</p>
    <p v-if="lastChange" class="panel-note">{{ lastChange }}</p>
  </section>
</template>

<style scoped>
.settings {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.settings li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.label {
  font-weight: 500;
  display: flex;
  gap: 8px;
  align-items: center;
}
.help {
  font-size: 12px;
  color: var(--muted);
}
</style>
