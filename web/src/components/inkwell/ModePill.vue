<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ modelValue: 'ask' | 'plan' | 'auto'; disabled?: boolean }>()
const emit = defineEmits<{ (event: 'update:modelValue', mode: 'ask' | 'plan' | 'auto'): void }>()
const modes = [
  { id: 'ask', label: '问', title: '只读：回答问题' },
  { id: 'plan', label: '谋', title: '只读：列出计划，不执行修改' },
  { id: 'auto', label: '做', title: '在工作区执行任务' },
] as const
const index = computed(() => modes.findIndex((mode) => mode.id === props.modelValue))
function navigate(event: KeyboardEvent) {
  if (props.disabled || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? 2
        : (index.value + (event.key === 'ArrowRight' ? 1 : -1) + modes.length) % modes.length
  emit('update:modelValue', modes[next].id)
  if (event.currentTarget instanceof HTMLElement)
    event.currentTarget.querySelectorAll<HTMLButtonElement>('button')[next]?.focus()
}
</script>
<template>
  <div
    class="mode-pill"
    :class="modelValue"
    :style="{ '--mode-index': index }"
    :aria-label="$t('工作方式')"
    @keydown="navigate"
  >
    <span
      class="mode-ink"
      aria-hidden="true"
    />
    <button
      v-for="mode in modes"
      :key="mode.id"
      :title="$t(mode.title)"
      :aria-pressed="modelValue === mode.id"
      :disabled="disabled"
      @click="emit('update:modelValue', mode.id)"
    >
      {{ $t(mode.label) }}
    </button>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.mode-pill {
  position: relative;
  display: inline-flex;
  padding: var(--space-1);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  background: var(--paper-2);
}
.mode-ink {
  position: absolute;
  inset-block: var(--space-1);
  left: var(--space-1);
  width: var(--composer-mode-width);
  background: var(--accent-selected);
  border-radius: var(--radius-sm);
  transform: translateX(calc(var(--mode-index) * 100%));
  transition: transform var(--motion-panel) var(--ease-spring);
}
.ask .mode-ink {
  background: var(--paper);
  box-shadow: inset 0 0 0 var(--hairline) var(--accent);
}
.plan .mode-ink {
  border-bottom: var(--focus-width) solid var(--accent);
}
.auto .mode-ink {
  background: var(--accent);
}
button {
  @include mixins.interactive-row;
  border: 0;
  position: relative;
  width: var(--composer-mode-width);
  padding-inline: var(--size-6);
  height: var(--composer-mode-height);
  color: var(--ink-2);
  background: var(--transparent);
  font-size: var(--text-sm);
}
button[aria-pressed='true'] {
  @include mixins.text-ink;
  background: var(--transparent);
  font-weight: var(--weight-semibold);
}
.auto button[aria-pressed='true'] {
  color: var(--on-accent);
}
</style>
