import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, command } from '@/lib/api'
import type { Approval } from '@/lib/types'

export const useApprovals = defineStore('approvals', () => {
  const pending = ref<Approval[]>([])
  const count = ref(0)

  async function load() {
    const r = await api<{ approvals: Approval[]; pending: number }>('/api/v1/approvals?status=pending')
    pending.value = r.approvals
    count.value = r.pending
  }

  /** Decisions go through the coordinator (cmd.coordinator.decide_approval); the inbox refreshes from the feed. */
  async function decide(approvalId: string, approved: boolean, note = '') {
    await command('approvals.decide', { approval_id: approvalId, approved, note })
    pending.value = pending.value.filter((a) => a.approval_id !== approvalId)
    count.value = Math.max(count.value - 1, 0)
  }

  return { pending, count, load, decide }
})
