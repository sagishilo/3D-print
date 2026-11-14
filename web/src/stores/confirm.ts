// One app-wide confirmation dialog: `await useConfirm().ask({...})` resolves true when confirmed.
import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ConfirmRequest {
  title: string
  text: string
  confirmLabel?: string
  danger?: boolean
  /** A statement the operator must tick before confirming ("The bed is empty"). */
  requireCheck?: string
}

export const useConfirm = defineStore('confirm', () => {
  const current = ref<ConfirmRequest | null>(null)
  let resolver: ((ok: boolean) => void) | null = null

  function ask(req: ConfirmRequest): Promise<boolean> {
    resolver?.(false)
    current.value = req
    return new Promise((resolve) => (resolver = resolve))
  }

  function answer(ok: boolean) {
    resolver?.(ok)
    resolver = null
    current.value = null
  }

  return { current, ask, answer }
})
