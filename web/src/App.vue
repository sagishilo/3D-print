<script setup lang="ts">
import { watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import ConfirmHost from '@/components/ui/ConfirmHost.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useAuth } from '@/stores/auth'

const auth = useAuth()
const route = useRoute()
const router = useRouter()

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
