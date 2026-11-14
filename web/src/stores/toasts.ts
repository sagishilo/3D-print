import { defineStore } from 'pinia'
import { ref } from 'vue'

import type { Tone } from '@/lib/types'

export interface Toast {
  id: number
  tone: Tone
  title: string
  text?: string
  sticky?: boolean // safety alerts stay until dismissed
}

export const useToasts = defineStore('toasts', () => {
  const items = ref<Toast[]>([])
  let next = 1

  function dismiss(id: number) {
    items.value = items.value.filter((t) => t.id !== id)
  }

  function push(toast: Omit<Toast, 'id'>, ttlMs = 6000): number {
    const id = next++
    items.value = [...items.value.slice(-5), { ...toast, id }]
    if (!toast.sticky) setTimeout(() => dismiss(id), ttlMs)
    return id
  }

  return {
    items,
    push,
    dismiss,
    ok: (title: string, text?: string) => push({ tone: 'ok', title, text }),
    info: (title: string, text?: string) => push({ tone: 'accent', title, text }),
    error: (title: string, text?: string) => push({ tone: 'danger', title, text }, 10000),
  }
})
