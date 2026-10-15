<script setup lang="ts">
import { computed, onDeactivated, onUnmounted, ref, watch } from 'vue'
import { useLayoutStore, panelWidths } from '@/stores/layout'
import { useLayout } from '@/composables/useLayout'
import { layoutPixels } from '@/utils/layout-media'
const props = withDefaults(
  defineProps<{
    side?: 'left' | 'right'
    orientation?: 'vertical' | 'horizontal'
    modelValue?: number
    minimum?: number
    maximum?: number
    defaultValue?: number
    label?: string
    hint?: string
  }>(),
  {
    side: 'left',
    orientation: 'vertical',
    // Omitted dimensions are derived from the orientation, panel and viewport below.
    modelValue: undefined,
    minimum: undefined,
    maximum: undefined,
    defaultValue: undefined,
    label: '',
    hint: '',
  },
)
const emit = defineEmits<{
  dragging: [active: boolean]
  'update:modelValue': [value: number]
  change: [value: number]
}>()
const layout = useLayoutStore()
const { isDragging: widthDragging, startDrag, stopDrag } = useLayout()
const horizontal = computed(() => props.orientation === 'horizontal')
const defaultLabel = computed(() =>
  horizontal.value
    ? '调整文件树与预览的高度'
    : props.side === 'left'
      ? '调整侧栏宽度'
      : '调整抽屉宽度',
)
const heightDragging = ref(false)
const isDragging = computed(() => widthDragging.value || heightDragging.value)
const minimum = computed(() =>
  horizontal.value ? (props.minimum ?? 0) : panelWidths[props.side].min,
)
const maximum = computed(() =>
  Math.max(
    minimum.value,
    horizontal.value
      ? (props.maximum ?? window.innerHeight)
      : Math.min(panelWidths[props.side].max, props.maximum ?? window.innerWidth),
  ),
)
const value = computed(() =>
  clamp(
    horizontal.value
      ? (props.modelValue ?? minimum.value)
      : props.side === 'left'
        ? layout.sidebarWidth
        : layout.fileExplorerWidth,
  ),
)
let captured: HTMLElement | null = null,
  capturedId = -1
let startY = 0,
  startHeight = 0,
  dragValue = 0
let heightPanel: HTMLElement | null = null
let moveEvent: 'mousemove' | 'pointermove' = 'mousemove'
let endEvent: 'mouseup' | 'pointerup' = 'mouseup'
let ownsFeedback = false
function feedback(active: boolean) {
  if (active) {
    document.documentElement.dataset.resizeAxis = horizontal.value ? 'row' : 'column'
    ownsFeedback = true
  } else if (ownsFeedback) {
    delete document.documentElement.dataset.resizeAxis
    ownsFeedback = false
  }
}
function clamp(next: number) {
  return Math.max(minimum.value, Math.min(maximum.value, next))
}
function release() {
  const target = captured,
    id = capturedId
  captured = null
  capturedId = -1
  if (id >= 0 && target?.hasPointerCapture(id)) target.releasePointerCapture(id)
}
function begin(event: MouseEvent | PointerEvent) {
  // Native mouse events avoid Electron's pointer-capture path; touch/pen keep it.
  if (event instanceof PointerEvent && event.pointerType === 'mouse') return
  if (event.button !== 0 || (event instanceof PointerEvent && !event.isPrimary)) return
  const target = event.currentTarget
  if (!(target instanceof HTMLElement)) return
  event.preventDefault()
  finish()
  target.focus({ preventScroll: true })
  captured = target
  capturedId = event instanceof PointerEvent ? event.pointerId : -1
  moveEvent = capturedId < 0 ? 'mousemove' : 'pointermove'
  endEvent = capturedId < 0 ? 'mouseup' : 'pointerup'
  // Document listeners keep resizing alive if native pointer capture is lost.
  if (capturedId >= 0) {
    try {
      target.setPointerCapture(capturedId)
    } catch {
      /* Document listeners still track movement. */
    }
  }
  if (horizontal.value) {
    startY = event.clientY
    heightPanel =
      target.previousElementSibling instanceof HTMLElement ? target.previousElementSibling : null
    // Start from the visible split, including flex/layout constraints.
    startHeight = dragValue = clamp(heightPanel?.getBoundingClientRect().height ?? value.value)
    heightDragging.value = true
    document.addEventListener(moveEvent, move, true)
    document.addEventListener(endEvent, finishPointer, true)
    document.addEventListener('pointercancel', finishPointer, true)
    document.addEventListener('visibilitychange', visibilityChanged)
    window.addEventListener('blur', finish)
    window.addEventListener('resize', finish)
    return
  }
  const actual = target.parentElement?.getBoundingClientRect().width ?? value.value
  startDrag(
    props.side,
    event.clientX,
    actual,
    maximum.value,
    capturedId < 0 ? undefined : capturedId,
    target.parentElement ?? undefined,
  )
}
function applyHeight(y: number) {
  dragValue = clamp(startHeight + y - startY)
  if (heightPanel) heightPanel.style.flexBasis = `${dragValue}px`
  else emit('update:modelValue', dragValue)
  captured?.setAttribute('aria-valuenow', String(Math.round(dragValue)))
}
function matches(event: MouseEvent | PointerEvent) {
  return capturedId < 0
    ? !(event instanceof PointerEvent) || event.pointerType === 'mouse'
    : event instanceof PointerEvent && event.pointerId === capturedId
}
function move(event: MouseEvent | PointerEvent) {
  if (!heightDragging.value || !matches(event)) return
  if (!(event.buttons & 1)) return finish()
  // No rAF gate or parent render for each pixel; commit the ratio on release.
  applyHeight(event.clientY)
}
function finish() {
  if (heightDragging.value) {
    emit('update:modelValue', clamp(dragValue))
    emit('change', clamp(dragValue))
    heightDragging.value = false
  }
  heightPanel = null
  document.removeEventListener(moveEvent, move, true)
  document.removeEventListener(endEvent, finishPointer, true)
  document.removeEventListener('pointercancel', finishPointer, true)
  document.removeEventListener('visibilitychange', visibilityChanged)
  window.removeEventListener('blur', finish)
  window.removeEventListener('resize', finish)
  stopDrag()
  release()
  feedback(false)
}
function visibilityChanged() {
  if (document.hidden) finish()
}
function finishPointer(event: MouseEvent | PointerEvent) {
  if (!matches(event)) return
  if (heightDragging.value && (event.type === 'pointerup' || event.type === 'mouseup'))
    applyHeight(event.clientY)
  finish()
}
function lostCapture(event: PointerEvent) {
  if (event.pointerId === capturedId) captured = null
}
function reset() {
  if (!horizontal.value) return layout.resetPanelWidth(props.side)
  const next = clamp(props.defaultValue ?? minimum.value)
  emit('update:modelValue', next)
  emit('change', next)
}
function onKey(event: KeyboardEvent) {
  const previous = horizontal.value ? 'ArrowUp' : 'ArrowLeft'
  const nextKey = horizontal.value ? 'ArrowDown' : 'ArrowRight'
  if (![previous, nextKey, 'Home', 'End', 'Enter'].includes(event.key)) return
  event.preventDefault()
  if (event.key === 'Enter') {
    reset()
    return
  }
  const direction =
    (event.key === nextKey ? 1 : -1) * (horizontal.value || props.side === 'left' ? 1 : -1)
  const next =
    event.key === 'Home'
      ? minimum.value
      : event.key === 'End'
        ? maximum.value
        : value.value +
          direction * layoutPixels(event.shiftKey ? '--resize-step-fast' : '--resize-step')
  const adjusted = clamp(next)
  if (horizontal.value) {
    emit('update:modelValue', adjusted)
    emit('change', adjusted)
    return
  }
  if (props.side === 'left') layout.setSidebarWidth(adjusted)
  else layout.setFileExplorerWidth(adjusted)
  layout.persistLayout()
}
watch(
  isDragging,
  (active) => {
    feedback(active)
    emit('dragging', active)
    if (!active) release()
  },
  { flush: 'sync' },
)
onDeactivated(finish)
onUnmounted(finish)
</script>
<template>
  <div
    class="resize-handle"
    :class="[horizontal ? 'horizontal' : side, { dragging: isDragging }]"
    role="separator"
    tabindex="0"
    :aria-orientation="orientation"
    :aria-label="label || $t(defaultLabel)"
    :aria-valuemin="minimum"
    :aria-valuemax="maximum"
    :aria-valuenow="Math.round(value)"
    :title="
      hint || (horizontal ? $t('拖动调整文件树和预览；双击还原') : $t('拖动调整宽度；双击还原'))
    "
    @pointerdown="begin"
    @mousedown="begin"
    @lostpointercapture="lostCapture"
    @keydown="onKey"
    @dblclick="reset"
  />
</template>
<style scoped lang="scss">
@use '@/styles/resize-handle';
</style>
