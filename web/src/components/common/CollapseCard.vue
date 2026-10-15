<script setup lang="ts">
import { ref, watch } from 'vue'
import IconSymbol from './IconSymbol.vue'

const props = withDefaults(
  defineProps<{
    title: string
    defaultExpanded?: boolean
    icon?: string
    variant?: 'default' | 'settings'
  }>(),
  {
    defaultExpanded: false,
    icon: 'activity',
    variant: 'default',
  },
)
const isExpanded = ref(props.defaultExpanded)
const bodyId = `collapse-${Array.from(crypto.getRandomValues(new Uint32Array(2)), (value) => value.toString(16)).join('-')}`
watch(
  () => props.defaultExpanded,
  (value) => {
    isExpanded.value = value
  },
)
</script>

<template>
  <section
    class="collapse-card"
    :data-variant="variant"
  >
    <button
      class="card-header"
      type="button"
      :aria-expanded="isExpanded"
      :aria-controls="variant === 'settings' || isExpanded ? bodyId : undefined"
      @click="isExpanded = !isExpanded"
    >
      <span class="title-icon">
        <IconSymbol
          :name="icon"
          :size="15"
        />
      </span>
      <span class="card-title">{{ title }}</span>
      <span
        v-if="$slots.summary"
        class="card-summary"
      >
        <slot name="summary" />
      </span>
      <IconSymbol
        name="chevron"
        :size="14"
        class="chevron"
        :class="{ expanded: isExpanded }"
      />
    </button>
    <div
      v-if="variant === 'settings' || isExpanded"
      v-show="isExpanded"
      :id="bodyId"
      class="card-body"
    >
      <slot />
    </div>
  </section>
</template>

<style scoped lang="scss">
@use '@/styles/mixins';
.collapse-card {
  width: min(var(--measure-760px), calc(100% - var(--rail-node-height)));
  margin: var(--size-10) auto 0;
  overflow: hidden;
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-lg);
  background: var(--paper-2);
}
.card-header {
  display: flex;
  width: 100%;
  height: var(--measure-42px);
  align-items: center;
  gap: var(--measure-9px);
  padding: 0 var(--space-4);
  border: 0;
  background: var(--transparent);
  color: var(--ink-2);
  cursor: pointer;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  text-align: left;
}
.card-header:hover {
  color: var(--ink);
  background: var(--paper-3);
}
.title-icon {
  display: grid;
  width: var(--measure-25px);
  height: var(--measure-25px);
  place-items: center;
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: var(--accent-soft);
}
.chevron {
  margin-left: auto;
  transition: transform var(--motion-pulse) var(--ease);
}
.chevron.expanded {
  transform: rotate(var(--measure-90deg));
}
.card-body {
  padding: var(--size-10) var(--measure-13px) var(--measure-13px);
  border-top: var(--hairline) solid var(--rule);
}
.card-title {
  min-width: 0;
  overflow-wrap: anywhere;
}
.card-summary {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-inline-start: auto;
  font-weight: var(--weight-normal);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.card-summary + .chevron {
  margin-inline-start: 0;
}
.collapse-card[data-variant='settings'] {
  width: 100%;
  margin: 0;
  overflow: visible;
  border-radius: var(--radius-sm);
  > .card-header {
    @include mixins.interactive-row;
    height: auto;
    min-height: var(--measure-46px);
    gap: var(--space-3);
    padding: var(--space-4);
    color: var(--ink);
    font-size: var(--text-sm);
    border-radius: var(--radius-sm);
  }
  > .card-header > .title-icon {
    background: var(--transparent);
  }
  > .card-header > .card-summary {
    color: var(--ink-2);
  }
  > .card-body {
    padding: var(--space-5);
  }
}
@media (prefers-reduced-motion: reduce) {
  .chevron {
    transition: none;
  }
}
</style>
