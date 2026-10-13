<script setup lang="ts">
import { computed, ref } from 'vue'

defineProps<{
  content: string
  id?: string
}>()

const hovered = ref(false), focused = ref(false), dismissed = ref(false)
const isVisible = computed(() => !dismissed.value && (hovered.value || focused.value))
function enter() { hovered.value = true; dismissed.value = false }
function focus() { focused.value = true; dismissed.value = false }
function blur(event: FocusEvent) {
  if (!(event.relatedTarget instanceof Node) || !(event.currentTarget as HTMLElement).contains(event.relatedTarget))
    focused.value = false
}
</script>

<template>
  <span
    class="tooltip-wrapper"
    @mouseenter="enter"
    @mouseleave="hovered = false"
    @focusin="focus"
    @focusout="blur"
    @keydown.esc.stop="dismissed = true"
  >
    <slot />
    <span
      :id="id"
      v-show="isVisible"
      class="tooltip"
      role="tooltip"
    >{{ content }}</span>
  </span>
</template>

<style scoped lang="scss">
.tooltip-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.tooltip {
  position: absolute;
  bottom: calc(100% + var(--space-2));
  inset-inline-start: 0;
  width: max-content;
  max-width: min(var(--measure-304px), calc(100vw - var(--space-5) * 2));
  padding: var(--space-3) var(--space-4);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  color: var(--ink);
  background: var(--paper-raised);
  box-shadow: var(--shadow-float);
  font-size: var(--text-xs);
  font-weight: var(--weight-normal);
  line-height: var(--leading-normal);
  white-space: normal;
  overflow-wrap: anywhere;
  z-index: var(--z-tooltip);
}

// Bridge the gap so the pointer can reach the help text without closing it.
.tooltip::after {
  content: '';
  position: absolute;
  inset-inline: 0;
  top: 100%;
  height: var(--space-2);
}
</style>
