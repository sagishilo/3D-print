<script setup lang="ts">
import { watch, watchEffect } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import ConfirmHost from '@/components/ui/ConfirmHost.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useAuth } from '@/stores/auth'
import { applyToDocument, usePrefs } from '@/stores/prefs'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const prefs = usePrefs()

// Theme and density of the signed-in person (the operating system's theme on the sign-in page).
watchEffect(() => applyToDocument({ theme: prefs.theme, density: prefs.density }))

// The session ended while the app was open (refresh token expired, logged out in another tab): back to sign in,
// then return to the same screen.
watch(
  () => auth.user,
  (user, previous) => {
    if (!user && previous && !route.meta.public) void router.replace({ name: 'login', query: { next: route.fullPath } })
  },
)
</script>

<template>
  <RouterView />
  <ToastHost />
  <ConfirmHost />
</template>
