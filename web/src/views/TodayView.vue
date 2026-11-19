<script setup lang="ts">
// Today (Wave 1 / M7): the home screen. Everything across the farm that waits for a person, most urgent first,
// grouped Now / Today / This week, each with one action (confirmed first) or a link to where it is handled.
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'
import NicheFilterBar from '@/components/ui/NicheFilterBar.vue'
import { vCan } from '@/directives/can'
import { errorText } from '@/lib/api'
import { GROUP_LABEL, KIND_ICON, type Priority, type TodayItem, waited } from '@/lib/today'
import { useAuth } from '@/stores/auth'
import { useConfirm } from '@/stores/confirm'
import { useToday } from '@/stores/today'
import { useToasts } from '@/stores/toasts'

const today = useToday()
const auth = useAuth()
const confirm = useConfirm()
const toasts = useToasts()
onMounted(() => void today.load())

const PRIORITIES: Priority[] = ['now', 'today', 'week']
const hello = computed(() => {
  const h = new Date().getHours()
  return `${h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'}${auth.displayName ? `, ${auth.displayName}` : ''}`
})

async function run(item: TodayItem) {
  if (item.action.type === 'approvals') {
    window.dispatchEvent(new CustomEvent('pf:open-approvals'))
    return
  }
  if (item.action.type !== 'command') return
  const ok = await confirm.ask({ title: item.title, text: item.action.confirm, confirmLabel: item.action.label })
  if (!ok) return
  try {
    await today.act(item)
    toasts.info(`${item.action.label}: sent`, 'Today updates when the agent confirms.')
  } catch (err) {
    toasts.error(`${item.action.label} failed`, errorText(err))
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Today</h1>
      <p>{{ hello }} · {{ today.count ? `${today.count} thing${today.count === 1 ? '' : 's'} wait for a person` : 'nothing waits for you' }}{{ today.urgent ? ` · ${today.urgent} now` : '' }}</p>
    </div>
    <button type="button" class="btn btn-sm" data-testid="today-refresh" @click="today.load()"><Icon name="refresh" :size="14" />Refresh</button>
  </div>
  <NicheFilterBar what="orders and stock of this niche · the farm-wide items stay" />
  <p v-if="today.error" class="err" role="alert">{{ today.error }}</p>

  <template v-for="p in PRIORITIES" :key="p">
    <section v-if="today.groups[p].length" class="panel group" :class="p" :data-testid="`today-${p}`" :data-tour="`today-${p}`">
      <div class="panel-head">
        <h2>{{ GROUP_LABEL[p].title }} <span class="n">{{ today.groups[p].length }}</span></h2>
        <span class="panel-note">{{ GROUP_LABEL[p].hint }}</span>
      </div>
      <ul class="items">
        <li v-for="i in today.groups[p]" :key="i.id" :data-testid="`today-item-${i.id}`">
          <span class="icon" :class="i.kind"><Icon :name="KIND_ICON[i.kind] ?? 'alert'" :size="16" /></span>
          <div class="what">
            <b>{{ i.title }}</b>
            <span class="muted small">{{ i.why }}<template v-if="i.age_s > 600"> · waiting {{ waited(i.age_s) }}</template><template v-if="i.expert"> · technical</template></span>
          </div>
          <RouterLink v-if="i.action.type === 'link'" class="btn btn-sm" :to="i.action.to" :data-testid="`today-go-${i.id}`">{{ i.action.label }}</RouterLink>
          <button v-else v-can="i.min_role" type="button" class="btn btn-sm" :class="{ 'btn-primary': p !== 'week' }" :data-testid="`today-act-${i.id}`" @click="run(i)">{{ i.action.label }}</button>
        </li>
      </ul>
    </section>
  </template>

  <EmptyState v-if="today.loaded && !today.count && !today.error" icon="check" title="All clear"
    text="No bed to clear, no order waiting, nothing low on stock. New work shows here the moment it appears." data-testid="today-clear" />
</template>

<style scoped>
.group {
  margin-bottom: var(--gap);
}
.group.now {
  border-color: var(--danger-text);
}
.n {
  font-size: 12px;
  color: var(--muted);
  font-weight: 500;
  margin-left: 6px;
}
.items {
  list-style: none;
  margin: 0;
  padding: 0;
}
.items li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid var(--border);
}
.items li:first-child {
  border-top: 0;
}
.icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--surface-2);
  color: var(--muted);
  flex: none;
}
.now .icon {
  color: var(--danger-text);
}
.what {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.what b {
  font-size: 14px;
}
.err {
  color: var(--danger-text);
}
</style>
