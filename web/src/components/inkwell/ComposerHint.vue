<script lang="ts">
// Shared between composers even when local storage is unavailable.
let shownThisLaunch = false
</script>
<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useUIStore } from '@/stores/ui'
const ui = useUIStore()
const visible = ref(false)
const key = import.meta.env.VITE_MOCK === '1' ? 'maigokite.send-hint-v2-demo' : 'maigokite.send-hint-v2'
let timer: ReturnType<typeof setTimeout> | undefined
function show() {
  if (shownThisLaunch) return
  shownThisLaunch = true
  try {
    if (localStorage.getItem(key) === 'seen') return
    localStorage.setItem(key, 'seen')
  } catch {
    // Still show only once this launch when storage is blocked.
  }
  visible.value = true
  const delay = Number.parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue('--composer-hint-duration'),
  )
  timer = setTimeout(() => { visible.value = false }, Number.isFinite(delay) ? delay : 5000)
}
onBeforeUnmount(() => { clearTimeout(timer) })
defineExpose({ show })
</script>
<template>
  <Transition name="composer-hint">
    <p
      v-if="visible"
      class="composer-hint"
      role="status"
      aria-live="polite"
    >
      {{ ui.appearance.enterBehavior === 'send'
        ? $t('Enter 发送 · Shift + Enter 换行')
        : $t('Alt+S 发送 · Enter 换行') }}
    </p>
  </Transition>
</template>
<style scoped lang="scss">
.composer-hint {
  position: absolute;
  bottom: calc(100% + var(--space-3));
  right: 0;
  max-width: 100%;
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  background: var(--paper-raised);
  box-shadow: var(--shadow-float);
  color: var(--ink-2);
  font-size: var(--text-xs);
  line-height: var(--leading-small);
  pointer-events: none;
  z-index: var(--z-menu);
}
.composer-hint-enter-active,
.composer-hint-leave-active {
  transition: opacity var(--motion-fast) var(--ease), transform var(--motion-fast) var(--ease);
}
.composer-hint-enter-from,
.composer-hint-leave-to {
  opacity: 0;
  transform: translateY(var(--space-2));
}
@media (prefers-reduced-motion: reduce) {
  .composer-hint-enter-active,
  .composer-hint-leave-active {
    transition: opacity var(--motion-reduced) var(--ease);
  }
  .composer-hint-enter-from,
  .composer-hint-leave-to {
    transform: none;
  }
}
</style>
