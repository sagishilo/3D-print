<script setup lang="ts">
// Social & Trends: the Trend Analyst's latest signals (products and niches rising or cooling, from sales and social
// intent) and its nightly insight. The full picture per niche is on the Niche scorecard.
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { api, errorText } from '@/lib/api'
import type { Signals } from '@/lib/analytics'
import { withNiche } from '@/lib/niches'
import { useAuth } from '@/stores/auth'
import { useNiche } from '@/stores/niche'

const auth = useAuth()
const niche = useNiche()
const data = ref<Signals | null>(null)
const error = ref('')

async function load() {
  try {
    data.value = await api<Signals>(withNiche('/api/v1/trend/signals', niche.current))
    error.value = ''
  } catch (err) {
    error.value = errorText(err)
  }
}
onMounted(load)
watch(() => niche.current, () => void load())

const signals = computed(() => (data.value?.signals ?? []).slice(0, 8))
const insight = computed(() => data.value?.insights[0] ?? null)
</script>

<template>
  <section class="panel trend" aria-label="Trend Analyst" data-testid="trend-panel" data-tour="trend-panel">
    <div class="panel-head">
      <h2>Trend Analyst</h2>
      <span class="panel-note">{{ data?.day ? `as of ${data.day}` : 'counts every hour' }}</span>
    </div>
    <p v-if="error" class="err small">{{ error }}</p>
    <ul v-if="signals.length" class="list">
      <li v-for="s in signals" :key="s.signal_id ?? s.subject" :data-testid="`signal-${s.subject}`">
        <span class="dir" :class="s.direction">{{ s.direction === 'rising' ? '▲' : '▼' }}</span>
        <span><b>{{ s.label ?? s.subject }}</b><span class="muted small"> · {{ s.scope === 'niche' ? 'niche' : s.subject }}</span><br />
          <span class="muted small">{{ s.evidence[0] }}</span></span>
      </li>
    </ul>
    <p v-else-if="data" class="empty">Nothing rising or cooling yet: trends need a few weeks of sales and captured posts.</p>
    <blockquote v-if="insight" class="insight" dir="rtl">{{ insight.text }}</blockquote>
    <RouterLink v-if="auth.can('operator')" class="small" to="/scorecard">Open the niche scorecard →</RouterLink>
  </section>
</template>

<style scoped>
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
}
.list li {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.dir.rising {
  color: var(--ok);
}
.dir.falling {
  color: var(--warn);
}
.insight {
  margin: 10px 0;
  padding: 8px 12px;
  border-right: 3px solid var(--accent-line);
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.err {
  color: var(--danger-text);
}
</style>
