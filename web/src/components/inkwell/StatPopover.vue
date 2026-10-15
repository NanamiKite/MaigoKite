<script setup lang="ts">
import { nextTick, onMounted, onScopeDispose, ref } from 'vue'
import { useDialog } from '@/composables/useDialog'
import { layoutPixels } from '@/utils/layout-media'
import IconSymbol from '@/components/common/IconSymbol.vue'
const props = withDefaults(
  defineProps<{
    label: string
    title?: string
    placement?: 'above' | 'below'
    toolbar?: boolean
  }>(),
  { title: '', placement: 'above', toolbar: false },
)
const trigger = ref<HTMLButtonElement | null>(null),
  panel = ref<HTMLDialogElement | null>(null)
const open = ref(false),
  position = ref({ left: '0px', top: '0px' })
useDialog(panel, () => open.value)
async function toggle() {
  open.value = !open.value
  if (!open.value) return
  await nextTick()
  const anchor = trigger.value?.getBoundingClientRect(),
    bounds = panel.value?.getBoundingClientRect()
  if (!anchor || !bounds) return
  const gap = layoutPixels('--space-3'),
    top = layoutPixels('--titlebar-height')
  position.value = {
    left:
      Math.max(
        gap,
        Math.min(
          innerWidth - bounds.width - gap,
          anchor.left + anchor.width / 2 - bounds.width / 2,
        ),
      ) + 'px',
    top:
      Math.max(
        top,
        props.placement === 'below'
          ? Math.min(innerHeight - bounds.height - gap, anchor.bottom + gap)
          : anchor.top - bounds.height - gap,
      ) + 'px',
  }
}
function backdrop(event: PointerEvent) {
  if (event.target !== panel.value) return
  const bounds = panel.value?.getBoundingClientRect()
  if (
    bounds &&
    (event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom)
  )
    close()
}
function close() {
  open.value = false
}
onMounted(() => window.addEventListener('resize', close))
onScopeDispose(() => window.removeEventListener('resize', close))
</script>
<template>
  <button
    ref="trigger"
    type="button"
    class="stat-pill"
    :class="{ 'stat-pill--toolbar': toolbar }"
    :title="title || label"
    :aria-label="label"
    :aria-expanded="open"
    aria-haspopup="dialog"
    @click="toggle"
  >
    <slot name="trigger" />
  </button>
  <Teleport to="body">
    <dialog
      ref="panel"
      class="stat-panel inkline-shell"
      :style="position"
      :aria-label="label"
      @cancel="close"
      @close="close"
      @pointerdown="backdrop"
    >
      <header>
        <h3>{{ label }}</h3>
        <button
          class="icon-button"
          :aria-label="$t('关闭')"
          @click="close"
        >
          <IconSymbol
            name="close"
            :size="16"
          />
        </button>
      </header>
      <slot />
    </dialog>
  </Teleport>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.stat-pill {
  @include mixins.interactive-row;
  display: inline-flex;
  align-items: center;
  gap: var(--size-6);
  border: 0;
  border-radius: var(--radius-pill);
  padding: var(--space-1) var(--space-3);
  background: var(--transparent);
  color: var(--ink-2);
  font-size: var(--text-xs);
  line-height: var(--leading-small);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  -webkit-app-region: no-drag;
}
.stat-pill[aria-expanded='true'] {
  background: var(--paper-2);
  color: var(--ink);
}
.stat-pill--toolbar {
  justify-content: center;
  flex: none;
  width: var(--titlebar-button-size);
  height: var(--titlebar-button-size);
  padding: 0;
  border: var(--hairline) solid var(--transparent);
  border-radius: var(--radius-sm);
}
.stat-pill--toolbar[aria-expanded='true'] {
  background: var(--accent-selected);
  color: var(--accent);
}
.stat-pill--toolbar:active {
  transform: scale(var(--press-scale));
}
.stat-panel {
  position: fixed;
  inset: auto;
  margin: 0;
  width: min(var(--measure-304px), calc(100vw - var(--space-5)));
  max-height: calc(100vh - var(--session-actions-space));
  overflow-y: auto;
  padding: var(--space-5);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-float);
  background: var(--paper);
  color: var(--ink-2);
  font-size: var(--text-xs);
  line-height: var(--leading-small);
  animation: paper-arrive var(--motion-fast) var(--ease-out) both;
  -webkit-app-region: no-drag;
}
.stat-panel::backdrop {
  background: var(--transparent);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
h3 {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink);
}
@media (prefers-reduced-motion: reduce) {
  .stat-panel {
    animation: paper-fade var(--motion-reduced) linear both;
  }
}
</style>
