<script setup lang="ts">
import IconSymbol from '@/components/common/IconSymbol.vue'

defineProps<{ model: string }>()
</script>

<template>
  <p
    class="model-switch-notice"
    role="note"
  >
    <IconSymbol
      name="cube"
      :size="14"
    />
    <span>{{ $t('模型已切换为 {0}', [model]) }}</span>
  </p>
</template>

<style scoped lang="scss">
.model-switch-notice {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  margin: var(--space-6) 0 0 calc(var(--rail-width) + var(--space-4));
  color: var(--ink-2);
  font-size: var(--text-xs);
  line-height: var(--leading-small);
}
.icon-symbol {
  flex: none;
  color: var(--accent);
}
span {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
