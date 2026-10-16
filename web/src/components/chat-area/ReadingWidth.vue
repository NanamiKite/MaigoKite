<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { layoutPixels } from '@/utils/layout-media'
const props = defineProps<{ host: HTMLElement | null }>()
const storageKey = (import.meta.env.VITE_MOCK === '1' ? 'demo_' : '') + 'maigokite_reading_width'
const width = ref(0),
  minimum = ref(0),
  maximum = ref(0),
  dragging = ref(false)
let preference: number | null = null,
  observer: ResizeObserver | undefined
let startX = 0,
  startWidth = 0,
  direction = 1,
  pending = 0,
  pointer = -1
try {
  const value = Number(localStorage.getItem(storageKey))
  if (Number.isFinite(value) && value > 0) preference = value
} catch {
  /* Width preferences are optional. */
}
function clamp(value: number) {
  return Math.max(minimum.value, Math.min(maximum.value, value))
}
function paint(value: number) {
  pending = clamp(value)
  props.host?.style.setProperty('--content-width', `${pending}px`)
  props.host?.querySelectorAll('.reading-width__handle').forEach((handle) => {
    handle.setAttribute('aria-valuenow', String(Math.round(pending)))
  })
}
function measure() {
  if (!props.host) return
  const gutter =
    Number.parseFloat(getComputedStyle(props.host).getPropertyValue('--chat-reading-gutter')) || 0
  maximum.value = Math.max(0, props.host.clientWidth - gutter * 2)
  minimum.value = Math.min(maximum.value, layoutPixels('--chat-reading-min'))
  const preferred =
    preference ??
    Math.min(
      layoutPixels('--chat-reading-default-max'),
      props.host.clientWidth * layoutPixels('--chat-reading-share'),
    )
  paint(preferred)
  width.value = pending
}
function persist() {
  try {
    if (preference === null) localStorage.removeItem(storageKey)
    else localStorage.setItem(storageKey, String(preference))
  } catch {
    /* Resizing still works without storage. */
  }
}
function finish() {
  if (dragging.value) {
    width.value = preference = pending
    persist()
    delete document.documentElement.dataset.resizeAxis
  }
  dragging.value = false
  document.removeEventListener('mousemove', move, true)
  document.removeEventListener('mouseup', end, true)
  document.removeEventListener('pointermove', move, true)
  document.removeEventListener('pointerup', end, true)
  document.removeEventListener('pointercancel', end, true)
  window.removeEventListener('blur', finish)
}
function matches(event: MouseEvent | PointerEvent) {
  return pointer < 0
    ? !(event instanceof PointerEvent)
    : event instanceof PointerEvent && event.pointerId === pointer
}
function move(event: MouseEvent | PointerEvent) {
  if (!matches(event)) return
  if (!(event.buttons & 1)) return finish()
  paint(startWidth + (event.clientX - startX) * direction * 2)
}
function end(event: MouseEvent | PointerEvent) {
  if (!matches(event)) return
  if (event.type !== 'pointercancel') paint(startWidth + (event.clientX - startX) * direction * 2)
  finish()
}
function begin(event: MouseEvent | PointerEvent, side: 'left' | 'right') {
  // Match the existing desktop splitters: mouse events on Linux, pointer events for touch/pen.
  if (event instanceof PointerEvent && (event.pointerType === 'mouse' || !event.isPrimary)) return
  if (event.button !== 0) return
  event.preventDefault()
  finish()
  ;(event.currentTarget as HTMLElement).focus({ preventScroll: true })
  pointer = event instanceof PointerEvent ? event.pointerId : -1
  startX = event.clientX
  startWidth = pending
  direction = side === 'right' ? 1 : -1
  dragging.value = true
  document.documentElement.dataset.resizeAxis = 'column'
  document.addEventListener(pointer < 0 ? 'mousemove' : 'pointermove', move, true)
  document.addEventListener(pointer < 0 ? 'mouseup' : 'pointerup', end, true)
  document.addEventListener('pointercancel', end, true)
  window.addEventListener('blur', finish)
}
function reset() {
  finish()
  preference = null
  persist()
  measure()
}
function key(event: KeyboardEvent, side: 'left' | 'right') {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter'].includes(event.key)) return
  event.preventDefault()
  if (event.key === 'Enter') return reset()
  const step = layoutPixels(event.shiftKey ? '--resize-step-fast' : '--resize-step')
  const sign = (event.key === 'ArrowRight' ? 1 : -1) * (side === 'right' ? 1 : -1)
  paint(
    event.key === 'Home'
      ? minimum.value
      : event.key === 'End'
        ? maximum.value
        : width.value + sign * step,
  )
  width.value = preference = pending
  persist()
}
watch(
  () => props.host,
  (host) => {
    observer?.disconnect()
    if (!host) return
    observer = new ResizeObserver(() => {
      finish()
      measure()
    })
    observer.observe(host)
    measure()
  },
  { immediate: true, flush: 'post' },
)
onBeforeUnmount(() => {
  finish()
  observer?.disconnect()
})
</script>
<template>
  <div
    class="reading-width"
    :class="{ dragging }"
  >
    <div
      v-for="side in ['left', 'right'] as const"
      :key="side"
      class="reading-width__handle"
      :class="side"
      role="separator"
      tabindex="0"
      aria-orientation="vertical"
      :aria-label="$t('调整正文宽度')"
      :aria-valuemin="Math.round(minimum)"
      :aria-valuemax="Math.round(maximum)"
      :aria-valuenow="Math.round(width)"
      :title="$t('拖动调整正文宽度；双击还原')"
      @mousedown="begin($event, side)"
      @pointerdown="begin($event, side)"
      @keydown="key($event, side)"
      @dblclick="reset"
    />
  </div>
</template>
<style scoped lang="scss">
.reading-width {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: var(--z-detail);
}
.reading-width__handle {
  position: absolute;
  top: var(--space-7);
  bottom: var(--space-7);
  width: var(--space-4);
  cursor: col-resize;
  touch-action: none;
  pointer-events: auto;
  -webkit-app-region: no-drag;
  &.left {
    left: max(0px, calc((100% - var(--content-width)) / 2 - var(--space-4)));
  }
  &.right {
    right: max(0px, calc((100% - var(--content-width)) / 2 - var(--space-4)));
  }
  &::after {
    content: '';
    position: absolute;
    top: 40%;
    left: 50%;
    width: var(--hairline);
    height: var(--space-9);
    background: var(--rule-strong, var(--rule));
    border-radius: var(--radius-pill);
    transition:
      opacity var(--motion-fast) var(--ease-out),
      transform var(--motion-fast) var(--ease-out);
    opacity: var(--resize-grip-rest-opacity);
  }
  &:hover::after,
  &:focus-visible::after,
  .dragging &::after {
    background: var(--accent);
    transform: scaleY(var(--resize-grip-active-scale));
    opacity: 1;
  }
}
</style>
