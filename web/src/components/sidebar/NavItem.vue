<script setup lang="ts">
import { useLayoutStore } from '@/stores/layout'
import IconSymbol from '@/components/common/IconSymbol.vue'
import { vTooltip } from '@/utils/tooltip'

defineProps<{ icon: string; to: string; label: string }>()
const layoutStore = useLayoutStore()
</script>

<template>
  <router-link
    v-tooltip="layoutStore.sidebarCollapsed ? $t(label) : undefined"
    :to="to"
    class="nav-item"
    :aria-label="$t(label)"
  >
    <IconSymbol
      :name="icon"
      :size="17"
    />
    <span
      v-show="!layoutStore.sidebarCollapsed"
      class="nav-label"
    >
      {{ $t(label) }}
    </span>
  </router-link>
</template>

<style scoped lang="scss">
.nav-item {
  display: flex;
  height: var(--measure-34px);
  align-items: center;
  gap: var(--size-10);
  padding: 0 var(--size-10);
  border-radius: var(--radius-md);
  color: var(--ink-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  transition:
    transform var(--motion-fast) var(--ease),
    opacity var(--motion-fast) var(--ease);
}
.nav-item:hover {
  color: var(--ink);
  background: var(--paper-3);
}
.nav-item:active {
  transform: scale(0.98);
}
.nav-item.router-link-exact-active {
  color: var(--ink);
  background: var(--paper-3);
  box-shadow: var(--shadow-card);
}
.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
