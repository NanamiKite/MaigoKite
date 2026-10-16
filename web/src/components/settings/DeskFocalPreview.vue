<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDeskStore } from '@/stores/desk'
const desk = useDeskStore()
const box = ref<HTMLElement | null>(null)
const focalStyle = computed(() => ({
  '--focal-x': `${desk.preferences.focal.x * 100}%`,
  '--focal-y': `${desk.preferences.focal.y * 100}%`,
}))
let pointer: number | null = null
function focal(event: PointerEvent) {
  if (pointer !== event.pointerId || !box.value) return
  const r = box.value.getBoundingClientRect()
  desk.change({
    focal: {
      x: Math.max(0, Math.min(1, (event.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (event.clientY - r.top) / r.height)),
    },
  })
}
function start(event: PointerEvent) {
  if (event.button !== 0) return
  pointer = event.pointerId
  box.value?.setPointerCapture(pointer)
  focal(event)
}
function end(event: PointerEvent) {
  if (pointer !== event.pointerId) return
  pointer = null
  if (box.value?.hasPointerCapture(event.pointerId))
    box.value.releasePointerCapture(event.pointerId)
}
function key(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home'].includes(event.key)) return
  event.preventDefault()
  const { x, y } = desk.preferences.focal,
    step = event.shiftKey ? 0.05 : 0.01
  desk.change({
    focal:
      event.key === 'Home'
        ? { x: 0.5, y: 0.5 }
        : {
            x: Math.max(
              0,
              Math.min(
                1,
                x + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0),
              ),
            ),
            y: Math.max(
              0,
              Math.min(
                1,
                y + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0),
              ),
            ),
          },
  })
}
</script>
<template>
  <div
    ref="box"
    class="desk-focus"
    :style="focalStyle"
    role="slider"
    tabindex="0"
    :aria-label="$t('背景焦点位置')"
    :aria-valuemin="0"
    :aria-valuemax="100"
    :aria-valuenow="Math.round(desk.preferences.focal.x * 100)"
    :aria-valuetext="
      $t('水平 {0}%，垂直 {1}%', [
        Math.round(desk.preferences.focal.x * 100),
        Math.round(desk.preferences.focal.y * 100),
      ])
    "
    @pointerdown="start"
    @pointermove="focal"
    @pointerup="end"
    @pointercancel="end"
    @lostpointercapture="pointer = null"
    @keydown="key"
  >
    <img
      :src="desk.clearUrl"
      alt=""
      draggable="false"
      @error="desk.imageFailed"
    />
    <span
      class="desk-focus-mark"
      aria-hidden="true"
    />
  </div>
  <p class="panel-copy">
    {{ $t('拖动选择焦点；方向键微调，Home 居中。') }}
  </p>
</template>
<style scoped lang="scss">
.desk-focus {
  position: relative;
  height: var(--desk-focus-height);
  overflow: hidden;
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  cursor: crosshair;
  touch-action: none;
}
.desk-focus img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: var(--desk-position);
  pointer-events: none;
}
.desk-focus-mark {
  position: absolute;
  left: var(--focal-x);
  top: var(--focal-y);
  width: var(--space-6);
  height: var(--space-6);
  border: var(--focus-width) solid var(--paper);
  box-shadow: 0 0 0 var(--hairline) var(--ink);
  border-radius: var(--radius-pill);
  transform: translate(-50%, -50%);
  pointer-events: none;
}
</style>
