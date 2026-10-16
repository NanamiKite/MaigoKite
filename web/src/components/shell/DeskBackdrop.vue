<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watchEffect } from 'vue'
import { useDeskStore } from '@/stores/desk'
const desk = useDeskStore()
const element = ref<HTMLElement | null>(null)
const root = shallowRef<HTMLElement | null>(null)
const stopPaint = watchEffect(() => {
  if (!root.value) return
  root.value.toggleAttribute('data-backdrop', desk.active)
  root.value.style.setProperty('--desk-clear', desk.clearUrl ? `url("${desk.clearUrl}")` : 'none')
  root.value.style.setProperty('--desk-paper-a', String(desk.paperAlpha))
  root.value.style.setProperty(
    '--desk-position',
    `${desk.preferences.focal.x * 100}% ${desk.preferences.focal.y * 100}%`,
  )
})
onMounted(() => {
  root.value = element.value?.parentElement || null
  desk.start()
})
onBeforeUnmount(() => {
  stopPaint()
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
