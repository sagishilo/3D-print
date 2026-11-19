// v-can="'operator'" (import { vCan } in a component's <script setup>): the control works only for that role and above. For everyone else it is disabled, with the
// reason as its tooltip. The gateway refuses the action anyway; this just avoids offering it.
import type { Directive } from 'vue'

import { needs, type Role } from '@/lib/roles'
import { useAuth } from '@/stores/auth'

function apply(el: HTMLElement, min: Role) {
  const allowed = useAuth().can(min)
  el.toggleAttribute('data-no-role', !allowed)
  if (allowed) {
    if (el.dataset.canTitle !== undefined) {
      el.title = el.dataset.canTitle
      delete el.dataset.canTitle
    }
    return
  }
  if (el.dataset.canTitle === undefined) el.dataset.canTitle = el.title
  el.title = needs(min)
  el.setAttribute('aria-disabled', 'true')
  if ('disabled' in el) (el as HTMLButtonElement).disabled = true
}

export const vCan: Directive<HTMLElement, Role> = {
  mounted: (el, binding) => apply(el, binding.value),
  updated: (el, binding) => apply(el, binding.value),
}
