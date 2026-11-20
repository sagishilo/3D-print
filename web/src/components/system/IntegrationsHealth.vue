<script setup lang="ts">
// System: each connector's health (Wave 2 / W2-0). Operators see it; owners manage connectors on Integrations.
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import { api } from '@/lib/api'
import type { ConnectorHealth } from '@/stores/integrations'
import { useAuth } from '@/stores/auth'

const { t } = useI18n()
const auth = useAuth()
const rows = ref<ConnectorHealth[]>([])
const down = ref(0)
const loaded = ref(false)

async function load() {
  try {
    const r = await api<{ connectors: ConnectorHealth[]; down: number }>('/api/v1/integrations/health')
    rows.value = r.connectors
    down.value = r.down
  } catch {
    /* the card simply stays empty */
  } finally {
    loaded.value = true
  }
}
onMounted(load)
defineExpose({ load })
const on = computed(() => rows.value.filter((c) => c.enabled))
</script>

<template>
  <section class="panel" data-testid="integrations-health" data-tour="integrations-health">
    <div class="panel-head">
      <h2>{{ t('systemCard.title') }}</h2>
      <RouterLink v-if="auth.can('owner')" class="small manage" to="/integrations">{{ t('systemCard.manage') }}</RouterLink>
    </div>
    <p v-if="loaded && !on.length" class="muted small">{{ t('systemCard.allOff') }}</p>
    <p v-else-if="down" class="bad small" role="status">{{ t('systemCard.problems', { n: down }) }}</p>
    <p v-else-if="loaded" class="ok small">{{ t('systemCard.allOk') }}</p>
    <ul v-if="on.length" class="list">
      <li v-for="c in on" :key="c.connector_id" :data-testid="`health-card-${c.connector_id}`">
        <span class="dot" :class="c.health" aria-hidden="true" />
        <span>{{ c.label }}</span>
        <span class="muted small">{{ t(`integrations.modes.${c.mode}`) }} · {{ t(`integrations.health.${c.health}`) }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.manage {
  margin-left: auto;
}
.list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.list li .small {
  margin-left: auto;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--idle);
}
.dot.ok {
  background: var(--ok);
}
.dot.failing,
.dot.down {
  background: var(--danger);
}
.ok {
  color: var(--ok);
}
.bad {
  color: var(--danger-text);
}
</style>
