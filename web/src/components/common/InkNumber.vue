<script setup lang="ts">
import { ref } from 'vue'
import IconSymbol from './IconSymbol.vue'

defineProps<{ disabled?: boolean }>()
const control = ref<HTMLElement | null>(null)
function step(direction: -1 | 1) {
  const input = control.value?.querySelector<HTMLInputElement>('input[type="number"]')
  if (!input || input.matches(':disabled') || input.readOnly) return
  const previous = input.value
  // The original input owns min/max/step, validation and the bound input handler.
  if (direction === 1) input.stepUp()
  else input.stepDown()
  if (input.value !== previous) input.dispatchEvent(new Event('input', { bubbles: true }))
  input.focus({ preventScroll: true })
}
</script>

<template>
  <span
    ref="control"
    class="ink-number"
  >
    <slot />
    <button
      type="button"
      class="ink-number-decrease"
      :disabled="disabled"
      :aria-label="$t('减少数值')"
      @click="step(-1)"
    >
      <IconSymbol
        name="minimize"
        :size="14"
      />
    </button>
    <button
      type="button"
      :disabled="disabled"
      :aria-label="$t('增加数值')"
      @click="step(1)"
    >
      <IconSymbol
        name="plus"
        :size="14"
      />
    </button>
  </span>
</template>
