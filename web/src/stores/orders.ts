// Orders: the board's cards, pipeline stats and plates. Changes go through commands; the coordinator answers on the
// live feed (stage changes, rejected moves), which reloads the board.
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, command, errorText } from '@/lib/api'
import type { OrderCard, OrderDetail, OrderStats, PlateRow } from '@/lib/order-types'
import type { Stage } from '@/lib/orders'

export const useOrders = defineStore('orders', () => {
  const cards = ref<OrderCard[]>([])
  const stats = ref<OrderStats | null>(null)
  const plates = ref<PlateRow[]>([])
  const error = ref('')
  const loadedAt = ref(0)
  let timer: ReturnType<typeof setTimeout> | null = null

  async function load() {
    try {
      const [o, s, p] = await Promise.all([
        api<{ orders: OrderCard[] }>('/api/v1/orders'),
        api<OrderStats>('/api/v1/orders/stats'),
        api<{ plates: PlateRow[] }>('/api/v1/orders/plates'),
      ])
      cards.value = o.orders
      stats.value = s
      plates.value = p.plates
      error.value = ''
      loadedAt.value = Date.now()
    } catch (err) {
      error.value = errorText(err)
    }
  }

  /** Several events arrive per move: reload once they settle. */
  function reloadSoon() {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), 300)
  }

  const detail = (id: string) => api<OrderDetail>(`/api/v1/orders/${encodeURIComponent(id)}`)
  const advance = (orderId: string, to: Stage, texts: Record<string, string> = {}) =>
    command('orders.advance', { order_id: orderId, to_state: to, texts })
  const qc = (orderId: string, passed: boolean, failedUnits: string[] = [], reason = '') =>
    command('orders.qc', { order_id: orderId, passed, failed_units: failedUnits, reason })
  const ship = (orderId: string, carrier: string, tracking: string) => command('orders.ship', { order_id: orderId, carrier, tracking })
  const cancel = (orderId: string, reason: string) => command('orders.cancel', { order_id: orderId, reason })
  const create = (body: Record<string, unknown>) => command('orders.create', body)

  return { cards, stats, plates, error, loadedAt, load, reloadSoon, detail, advance, qc, ship, cancel, create }
})
