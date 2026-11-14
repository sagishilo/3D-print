<script setup lang="ts">
// One printer camera: MJPEG through the gateway (go2rtc), drawn frame by frame so the tile knows the frame age.
// Streams only while the tile is on screen and the browser tab is visible (cameras on P1/A1 close when unwatched).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import StatusPill from '@/components/ui/StatusPill.vue'
import { ago } from '@/lib/format'
import { type CameraState, MjpegReader } from '@/lib/mjpeg'
import type { Tone } from '@/lib/types'
import { useLive } from '@/stores/live'

const props = defineProps<{ printerId: string; available: boolean; protocol: string; simulated?: boolean; height?: number }>()

const live = useLive()
const root = ref<HTMLElement | null>(null)
const frameUrl = ref('')
const state = ref<CameraState>('stopped')
const detail = ref('')
const lastFrameAt = ref(0)
let reader: MjpegReader | null = null
let visible = false
let observer: IntersectionObserver | null = null

const url = computed(() => `/api/v1/cameras/${encodeURIComponent(props.printerId)}/mjpeg`)

function wanted(): boolean {
  return props.available && visible && !document.hidden
}

function sync() {
  if (wanted() && !reader) {
    reader = new MjpegReader(
      url.value,
      (blob) => {
        const next = URL.createObjectURL(blob)
        const old = frameUrl.value
        frameUrl.value = next
        lastFrameAt.value = Date.now()
        if (old) URL.revokeObjectURL(old)
      },
      (s, d) => {
        state.value = s
        detail.value = d
      },
    )
    reader.start()
  } else if (!wanted() && reader) {
    reader.stop()
    reader = null
  }
}

onMounted(() => {
  if ('IntersectionObserver' in window && root.value) {
    observer = new IntersectionObserver((entries) => {
      visible = entries.some((e) => e.isIntersecting)
      sync()
    })
    observer.observe(root.value)
  } else {
    visible = true
    sync()
  }
  document.addEventListener('visibilitychange', sync)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  document.removeEventListener('visibilitychange', sync)
  reader?.stop()
  reader = null
  if (frameUrl.value) URL.revokeObjectURL(frameUrl.value)
})

watch(() => props.available, sync)

const view = computed<{ tone: Tone; label: string; note: string }>(() => {
  if (!props.available) return { tone: 'idle', label: 'NO CAMERA', note: 'No camera configured (go2rtc.yaml)' }
  switch (state.value) {
    case 'live':
      return { tone: 'ok', label: 'LIVE', note: '' }
    case 'stalled':
      return { tone: 'warn', label: 'STALLED', note: 'No new frame - reconnecting…' }
    case 'error':
      return { tone: 'danger', label: 'OFFLINE', note: detail.value || 'Camera unreachable' }
    case 'connecting':
      return { tone: 'warn', label: lastFrameAt.value ? 'RECONNECTING' : 'CONNECTING', note: 'Connecting to the camera…' }
    default:
      return { tone: 'idle', label: 'STANDBY', note: 'The stream stops while the tile is off screen' }
  }
})
const frameAge = computed(() => (lastFrameAt.value ? `frame ${ago(lastFrameAt.value, live.now)}` : ''))
const snapshotHref = computed(() => `/api/v1/cameras/${encodeURIComponent(props.printerId)}/snapshot.jpg`)
</script>

<template>
  <div ref="root" class="cam" :style="{ height: `${height ?? 168}px` }" role="img" :aria-label="`${printerId} camera: ${view.label}`">
    <img v-if="frameUrl" :src="frameUrl" alt="" :class="{ held: state !== 'live' }" />
    <span v-if="!frameUrl || state !== 'live'" class="note">{{ view.note || (frameUrl ? '' : 'Waiting for the first frame…') }}</span>
    <span class="tl">
      <StatusPill :tone="view.tone" :label="view.label" />
      <span v-if="frameAge" class="chip">{{ frameAge }}</span>
    </span>
    <span class="tr">
      <span v-if="simulated" class="chip warn">SIM</span>
      <span class="chip">{{ protocol }}</span>
    </span>
    <a v-if="available" class="snap" :href="snapshotHref" target="_blank" rel="noopener" :aria-label="`Open a snapshot of ${printerId}`">
      <Icon name="camera" :size="14" />
    </a>
  </div>
</template>

<style scoped>
.cam {
  position: relative;
  background: repeating-linear-gradient(135deg, var(--surface-2) 0 10px, var(--surface) 10px 20px);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
img.held {
  opacity: 0.45;
  filter: grayscale(0.6);
}
.note {
  position: relative;
  font: 500 12px var(--font);
  color: var(--muted);
  background: var(--bg);
  padding: 4px 10px;
  border-radius: 6px;
  max-width: 80%;
  text-align: center;
}
.tl {
  position: absolute;
  top: 10px;
  left: 10px;
  display: flex;
  gap: 6px;
}
.tr {
  position: absolute;
  top: 10px;
  right: 10px;
  display: flex;
  gap: 6px;
}
.snap {
  position: absolute;
  bottom: 10px;
  right: 10px;
  display: inline-flex;
  padding: 6px;
  border-radius: 6px;
  background: var(--bg);
  color: var(--muted);
  border: 1px solid var(--border);
}
.snap:hover {
  color: var(--text);
}
</style>
