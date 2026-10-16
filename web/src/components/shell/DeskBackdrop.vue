<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch, watchEffect } from 'vue'
import { useDeskStore } from '@/stores/desk'
import { useUIStore } from '@/stores/ui'
import { readDeskPalette } from '@/utils/desk-palette'
const desk = useDeskStore(),
  ui = useUIStore()
const element = ref<HTMLElement | null>(null)
const root = shallowRef<HTMLElement | null>(null)
const stopPalette = watch(
  [root, () => desk.image, () => ui.theme, () => ui.appearance.accent],
  () => {
    if (root.value)
      void desk.calculate(readDeskPalette(root.value), ui.theme, readDeskPalette(root.value, true))
  },
  { flush: 'post' },
)
const stopPaint = watchEffect(() => {
  if (!root.value) return
  root.value.toggleAttribute('data-backdrop', desk.active)
  root.value.style.setProperty('--desk-clear', desk.clearUrl ? `url("${desk.clearUrl}")` : 'none')
  root.value.style.setProperty('--desk-soft', desk.softUrl ? `url("${desk.softUrl}")` : 'none')
  root.value.style.setProperty('--plate-a', String(desk.alpha))
  root.value.style.setProperty('--chat-plate-a', String(desk.chatAlpha))
  root.value.style.setProperty(
    '--desk-position',
    `${desk.preferences.focal.x * 100}% ${desk.preferences.focal.y * 100}%`,
  )
})
// Hide paper translucency synchronously before a theme/ink swap; asynchronous
// bitmap decoding must never expose old-theme limits for even one frame.
const stopTheme = watch(
  [() => ui.theme, () => ui.appearance.accent],
  () => {
    root.value?.removeAttribute('data-backdrop')
  },
  { flush: 'sync' },
)
onMounted(() => {
  root.value = element.value?.parentElement || null
  desk.start()
})
onBeforeUnmount(() => {
  stopPalette()
  stopPaint()
  stopTheme()
  desk.stop()
  root.value?.removeAttribute('data-backdrop')
})
</script>
<template>
  <div
    ref="element"
    class="backdrop"
    aria-hidden="true"
  >
    <img
      v-if="desk.clearUrl"
      :src="desk.clearUrl"
      alt=""
      @error="desk.imageFailed"
    />
  </div>
  <p
    v-if="desk.notice"
    class="desk-toast"
    role="status"
  >
    {{ $t(desk.notice) }}
    <button
      class="icon-button"
      :aria-label="$t('关闭')"
      @click="desk.notice = ''"
    >
      ×
    </button>
  </p>
</template>
