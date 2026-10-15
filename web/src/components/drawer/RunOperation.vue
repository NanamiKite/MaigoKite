<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Step } from '@/api/events'
import IconSymbol from '@/components/common/IconSymbol.vue'
import RailNodeDetail from '@/components/rail/RailNodeDetail.vue'
import Pulse from '@/components/shell/Pulse.vue'
const props = defineProps<{ step: Step; ended?: boolean }>()
const expanded = ref(props.step.status === 'failed')
const active = computed(() => !props.ended && ['running', 'pending'].includes(props.step.status))
watch(
  () => props.step.status,
  (status) => {
    if (status === 'failed') expanded.value = true
  },
)
</script>
<template>
  <div class="run-operation">
    <button
      type="button"
      class="operation-heading"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <Pulse :state="step.status === 'failed' ? 'error' : active ? 'acting' : 'idle'" />
      <span>{{ step.title }}</span>
      <small>{{ step.tool }}</small>
      <IconSymbol name="chevron" :size="12" :class="{ expanded }" />
    </button>
    <RailNodeDetail v-if="expanded" :steps="[step]" :ended="ended" />
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.operation-heading {
  @include mixins.interactive-row;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-width: 0;
  padding: var(--space-3) var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--transparent);
  color: var(--ink-2);
  text-align: start;
  font-size: var(--text-xs);
  span {
    flex: 1;
    overflow-wrap: anywhere;
  }
  small {
    font: inherit;
  }
  svg {
    flex-shrink: 0;
  }
}
.expanded {
  transform: rotate(var(--measure-90deg));
}
</style>
