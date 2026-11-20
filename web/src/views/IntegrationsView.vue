<script setup lang="ts">
// Integrations (Wave 2 / W2-0, owner): every outside service as a card: on/off, test data or the live service, how
// often it syncs, whether its secrets are set (never their values), its health and last syncs, Run now. Below, the
// notifications outbox. All words come from the i18n catalogue (src/i18n/en.ts), ready for Hebrew in W2-D.
import { onMounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import { errorText } from '@/lib/api'
import { ago, dateTime } from '@/lib/format'
import { useConfirm } from '@/stores/confirm'
import { type Connector, type SettingValue, useIntegrations } from '@/stores/integrations'
import { useToasts } from '@/stores/toasts'

const { t } = useI18n()
const integrations = useIntegrations()
const confirm = useConfirm()
const toasts = useToasts()
onMounted(() => void integrations.load())

const INTERVALS = [60, 300, 900, 3600] as const

async function change(c: Connector, body: { enabled?: boolean; mode?: 'fake' | 'live'; interval_s?: number }) {
  if (body.mode === 'live') {
    const ok = await confirm.ask({ title: t('integrations.confirmLive', { name: c.label }), text: t('integrations.confirmLiveText'), confirmLabel: t('integrations.modes.live') })
    if (!ok) return
  }
  try {
    await integrations.configure(c.connector_id, body)
    toasts.info(t('integrations.saved'), c.label)
    integrations.reloadSoon(800)
  } catch (err) {
    toasts.error(c.label, errorText(err))
  }
}

async function runNow(c: Connector) {
  try {
    await integrations.runNow(c.connector_id)
    toasts.info(t('integrations.running'), c.label)
    integrations.reloadSoon(1500)
  } catch (err) {
    toasts.error(c.label, errorText(err))
  }
}

// W2-A: a connector's own settings (the Shopify shop address, …): edited in a draft, saved together
const drafts = reactive<Record<string, Record<string, SettingValue>>>({})
const draft = (c: Connector) => (drafts[c.connector_id] ??= { ...c.settings })
const dirty = (c: Connector) => Object.entries(draft(c)).some(([k, v]) => v !== c.settings[k])

async function saveSettings(c: Connector) {
  const changed = Object.fromEntries(Object.entries(draft(c)).filter(([k, v]) => v !== c.settings[k]))
  try {
    await integrations.configure(c.connector_id, { settings: changed })
    toasts.ok(t('storeSettings.saved'), c.label)
    delete drafts[c.connector_id]
    integrations.reloadSoon(800)
  } catch (err) {
    toasts.error(c.label, errorText(err))
  }
}

async function newTestOrders(c: Connector) {
  try {
    await integrations.configure(c.connector_id, { settings: { fake_batch: Number(c.settings.fake_batch ?? 1) + 1 } })
    await integrations.runNow(c.connector_id)
    toasts.info(t('integrations.running'), c.label)
    delete drafts[c.connector_id]
    integrations.reloadSoon(1500)
  } catch (err) {
    toasts.error(c.label, errorText(err))
  }
}

async function testAlert(c: Connector) {
  try {
    await integrations.testAlert(c.connector_id)
    toasts.info(t('integrations.testAsked'), t('integrations.testText'))
    integrations.reloadSoon(6000)
  } catch (err) {
    toasts.error(c.label, errorText(err))
  }
}

const since = (iso: string | null) => (iso ? t('common.ago', { t: ago(new Date(iso).getTime(), Date.now()) }) : t('common.never'))
</script>

<template>
  <div class="page-head">
    <div>
      <h1>{{ t('integrations.title') }}</h1>
      <p>{{ t('integrations.subtitle') }}</p>
    </div>
  </div>
  <p class="note small muted" data-tour="integrations-lan"><Icon name="lock" :size="14" />{{ t('integrations.lanNote') }}</p>
  <p v-if="integrations.error" class="err" role="alert">{{ integrations.error }}</p>

  <div class="cards" data-tour="integrations-cards">
    <article v-for="c in integrations.connectors" :key="c.connector_id" class="card" :class="`h-${c.health}`" :data-testid="`connector-${c.connector_id}`">
      <header>
        <span class="kind">{{ t(`integrations.kinds.${c.kind}`) }}</span>
        <h2>{{ c.label }}</h2>
        <span class="health" :class="c.health" :data-testid="`health-${c.connector_id}`">{{ t(`integrations.health.${c.health}`) }}</span>
      </header>

      <div class="controls">
        <label class="switch">
          <input type="checkbox" :checked="c.enabled" :data-testid="`enable-${c.connector_id}`" @change="change(c, { enabled: ($event.target as HTMLInputElement).checked })" />
          {{ t('integrations.enabled') }}
        </label>
        <label>{{ t('integrations.mode') }}
          <select class="select" :value="c.mode" :data-testid="`mode-${c.connector_id}`" @change="change(c, { mode: ($event.target as HTMLSelectElement).value as 'fake' | 'live' })">
            <option value="fake">{{ t('integrations.modes.fake') }}</option>
            <option value="live">{{ t('integrations.modes.live') }}</option>
          </select>
        </label>
        <label>{{ t('integrations.interval') }}
          <select class="select" :value="c.interval_s" :data-testid="`interval-${c.connector_id}`" @change="change(c, { interval_s: Number(($event.target as HTMLSelectElement).value) })">
            <option v-for="s in INTERVALS" :key="s" :value="s">{{ t(`integrations.intervals.s${s}`) }}</option>
            <option v-if="!INTERVALS.includes(c.interval_s as (typeof INTERVALS)[number])" :value="c.interval_s">{{ t('common.every', { interval: t('common.seconds', { n: c.interval_s }) }) }}</option>
          </select>
        </label>
        <button type="button" class="btn btn-sm" :disabled="!c.enabled" :data-testid="`run-${c.connector_id}`" @click="runNow(c)"><Icon name="refresh" :size="14" />{{ t('integrations.runNow') }}</button>
        <button v-if="c.kind === 'alerts'" type="button" class="btn btn-sm" :disabled="!c.enabled" :data-testid="`test-alert-${c.connector_id}`" @click="testAlert(c)">{{ t('integrations.testAlert') }}</button>
      </div>

      <dl class="facts">
        <div><dt>{{ t('integrations.lastSync') }}</dt><dd :data-testid="`last-sync-${c.connector_id}`">{{ since(c.last_ok_at) }}</dd></div>
        <div><dt>{{ t('integrations.nextSync') }}</dt><dd>{{ c.enabled && c.next_run_at ? dateTime(c.next_run_at) : '—' }}</dd></div>
        <div><dt>{{ t('integrations.mapped') }}</dt><dd :data-testid="`mapped-${c.connector_id}`">{{ c.mapped }}</dd></div>
      </dl>
      <p v-if="c.consecutive_failures" class="warn small" role="status">{{ t('integrations.failedTimes', { n: c.consecutive_failures }) }}<template v-if="c.last_error"> · {{ c.last_error }}</template></p>

      <form v-if="c.settings_schema?.length" class="settings" :data-testid="`settings-${c.connector_id}`" @submit.prevent="saveSettings(c)">
        <b class="small">{{ t('storeSettings.title') }}</b>
        <template v-for="f in c.settings_schema" :key="f.key">
          <label v-if="f.type === 'bool'" class="check"><input v-model="draft(c)[f.key]" type="checkbox" :data-testid="`setting-${c.connector_id}-${f.key}`" />{{ f.label }}</label>
          <label v-else-if="f.key !== 'fake_batch'">{{ f.label }}
            <input v-if="f.type === 'int'" v-model.number="draft(c)[f.key]" type="number" class="input" :data-testid="`setting-${c.connector_id}-${f.key}`" />
            <input v-else v-model.trim="draft(c)[f.key]" class="input" :data-testid="`setting-${c.connector_id}-${f.key}`" />
          </label>
        </template>
        <div class="row">
          <button type="submit" class="btn btn-sm" :disabled="!dirty(c)" :data-testid="`settings-save-${c.connector_id}`">{{ t('storeSettings.save') }}</button>
          <button v-if="c.kind === 'store' && c.mode === 'fake' && 'fake_batch' in c.settings" type="button" class="btn btn-sm" :disabled="!c.enabled" :title="t('storeSettings.newTestOrdersHelp')" :data-testid="`test-orders-${c.connector_id}`" @click="newTestOrders(c)">{{ t('storeSettings.newTestOrders') }}</button>
        </div>
      </form>

      <div class="secrets">
        <b class="small">{{ t('integrations.secrets') }}</b>
        <ul>
          <li v-for="(set, key) in c.secrets_status" :key="key" :data-testid="`secret-${key}`">
            <span class="mono">{{ key }}</span>
            <span class="pill" :class="set ? 'set' : 'missing'">{{ set ? t('integrations.secretSet') : t('integrations.secretMissing') }}</span>
          </li>
        </ul>
        <p v-if="c.mode === 'live' && c.secrets_missing.length" class="warn small">{{ t('integrations.liveBlocked', { keys: c.secrets_missing.join(', ') }) }}</p>
        <p class="muted small">{{ t('integrations.secretsHelp') }}</p>
      </div>

      <div class="runs">
        <b class="small">{{ t('integrations.runs') }}</b>
        <ul v-if="integrations.runs[c.connector_id]?.length">
          <li v-for="r in integrations.runs[c.connector_id]" :key="r.id" class="small">
            <span :class="r.ok ? 'run-ok' : r.ok === false ? 'run-bad' : 'muted'">{{ r.ok ? '✓' : r.ok === false ? '✕' : '…' }}</span>
            <span class="muted">{{ dateTime(r.started_at) }}</span>
            <span v-if="r.ok">{{ t('integrations.items', { n: r.items }) }} · {{ t('integrations.created', { n: r.created }) }} · {{ t('integrations.updated', { n: r.updated }) }}</span>
            <span v-else-if="r.error" class="run-bad">{{ r.error }}</span>
          </li>
        </ul>
        <p v-else class="muted small">{{ t('integrations.noRuns') }}</p>
      </div>
    </article>
  </div>
  <EmptyState v-if="integrations.loaded && !integrations.connectors.length && !integrations.error" icon="external" :title="t('integrations.empty')" />

  <section class="panel outbox" data-testid="outbox" data-tour="integrations-outbox">
    <div class="panel-head"><h2>{{ t('notifications.title') }}</h2><span class="panel-note">{{ t('notifications.subtitle') }}</span></div>
    <ul v-if="integrations.notifications.length" class="notes">
      <li v-for="n in integrations.notifications" :key="n.id" :class="n.severity" :data-testid="`note-${n.id}`">
        <span class="sev" :class="n.severity">{{ t(`notifications.severity.${n.severity}`) }}</span>
        <span class="what"><b>{{ n.title }}</b><span v-if="n.body" class="muted small">{{ n.body }}</span></span>
        <span class="muted small">{{ dateTime(n.created_at) }} · {{ t(`notifications.status.${n.status}`) }}<template v-if="n.channels?.length"> · {{ t('notifications.sentTo', { list: n.channels.join(', ') }) }}</template></span>
        <RouterLink v-if="n.link" class="btn btn-sm" :to="n.link">{{ t('common.open') }}</RouterLink>
      </li>
    </ul>
    <p v-else class="muted">{{ t('notifications.none') }}</p>
  </section>
</template>

<style scoped>
.note {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 0 0 var(--gap);
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--gap);
  margin-bottom: var(--gap);
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
.card.h-failing,
.card.h-down {
  box-shadow: inset calc(3px * var(--dir-x, 1)) 0 0 var(--danger-text);
}
.card header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.card h2 {
  margin: 0;
  font-size: 15px;
}
.kind {
  font-size: 11px;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 1px 8px;
}
.health {
  margin-inline-start: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}
.health.ok {
  color: var(--ok);
}
.health.failing,
.health.down {
  color: var(--danger-text);
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
}
.controls label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.controls .switch {
  flex-direction: row;
  align-items: center;
  color: var(--text);
  font-size: 13px;
}
.facts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin: 0;
}
.facts div {
  display: flex;
  flex-direction: column;
}
dt {
  font-size: 11px;
  color: var(--muted);
}
dd {
  margin: 0;
  font-size: 13px;
}
.settings {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.settings label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.settings label.check {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  color: var(--text);
}
.settings .row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.secrets ul,
.runs ul {
  list-style: none;
  margin: 6px 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.secrets li {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}
.pill {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--muted);
}
.pill.set {
  color: var(--ok);
}
.runs li {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.run-ok {
  color: var(--ok);
}
.run-bad,
.warn {
  color: var(--danger-text);
}
.notes {
  list-style: none;
  margin: 0;
  padding: 0;
}
.notes li {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  border-top: 1px solid var(--border);
}
.notes li:first-child {
  border-top: 0;
}
.sev {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
}
.sev.critical {
  color: var(--danger-text);
}
.sev.warning {
  color: var(--warn);
}
.what {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.err {
  color: var(--danger-text);
}
@media (max-width: 640px) {
  .cards {
    grid-template-columns: minmax(0, 1fr);
  }
  .notes li {
    grid-template-columns: 1fr auto;
  }
}
</style>
