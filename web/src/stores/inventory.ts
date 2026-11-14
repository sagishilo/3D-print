// Filament: spools, reservations, the reorder list, alerts, purchase orders and daily use. Changes are commands; the
// inventory agent answers on the live feed, which reloads this store.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, command, errorText } from '@/lib/api'
import type { InventoryAlert, MaterialRow, PurchaseOrder, Reservation, Spool, UsageRow } from '@/lib/filament'

export const useInventory = defineStore('inventory', () => {
  const spools = ref<Spool[]>([])
  const reservations = ref<Reservation[]>([])
  const materials = ref<MaterialRow[]>([])
  const alerts = ref<InventoryAlert[]>([])
  const pos = ref<PurchaseOrder[]>([])
  const spend30d = ref(0)
  const ordered30d = ref(0)
  const usage = ref<UsageRow[]>([])
  const error = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null

  async function loadAlerts() {
    try {
      alerts.value = (await api<{ alerts: InventoryAlert[] }>('/api/v1/inventory/alerts')).alerts
    } catch {
      /* the badge simply stays as it was */
    }
  }

  async function load() {
    try {
      const [s, m, a, p, u] = await Promise.all([
        api<{ spools: Spool[]; reservations: Reservation[] }>('/api/v1/inventory/spools?include_empty=true'),
        api<{ materials: MaterialRow[] }>('/api/v1/inventory/materials'),
        api<{ alerts: InventoryAlert[] }>('/api/v1/inventory/alerts'),
        api<{ pos: PurchaseOrder[]; spend_30d_agorot: number; ordered_30d: number }>('/api/v1/inventory/pos'),
        api<{ usage: UsageRow[] }>('/api/v1/inventory/usage?days=14'),
      ])
      spools.value = s.spools
      reservations.value = s.reservations
      materials.value = m.materials
      alerts.value = a.alerts
      pos.value = p.pos
      spend30d.value = p.spend_30d_agorot
      ordered30d.value = p.ordered_30d
      usage.value = u.usage
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    }
  }

  function reloadSoon() {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), 300)
  }

  const openAlerts = computed(() => alerts.value.filter((a) => !a.resolved_at))

  return {
    spools, reservations, materials, alerts, pos, spend30d, ordered30d, usage, error, openAlerts,
    load, loadAlerts, reloadSoon,
    register: (body: Record<string, unknown>) => command('spools.register', body),
    adjust: (spoolId: string, grams: number, reason = 'weighed') => command('spools.adjust', { spool_id: spoolId, remaining_grams: grams, reason }),
    loadSpool: (spoolId: string, printerId: string | null, slot = '', location = '') =>
      command('spools.load', { spool_id: spoolId, printer_id: printerId, slot, location }),
    setRule: (body: Record<string, unknown>) => command('inventory.rule', body),
    reorder: (material: string, color: string, spools?: number) => command('inventory.reorder', { material, color, ...(spools ? { spools } : {}) }),
    ackAlert: (alertId: string) => command('inventory.ack_alert', { alert_id: alertId }),
    updatePo: (poId: string, spoolsN: number) => command('po.update', { po_id: poId, spools: spoolsN }),
    confirmOrdered: (poId: string, supplier: string, totalAgorot: number) =>
      command('po.confirm_ordered', { po_id: poId, supplier, total_agorot: totalAgorot }),
    receive: (poId: string, location: string) => command('po.receive', { po_id: poId, location }),
  }
})
