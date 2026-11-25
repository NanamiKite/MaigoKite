<script setup lang="ts">
import { useUIStore } from '@/stores/ui'
import IconSymbol from '@/components/common/IconSymbol.vue'
import { vTooltip } from '@/utils/tooltip'
defineProps<{ pendingCount: number; sidebarOverlay?: boolean }>()
const ui = useUIStore()
</script>
<template>
  <button
    v-tooltip="$t(ui.layout.sidebarCollapsed ? '展开侧栏' : '收起侧栏')"
    class="sidebar-toggle"
    type="button"
    :aria-label="$t(ui.layout.sidebarCollapsed ? '展开侧栏' : '收起侧栏')"
    :aria-expanded="!ui.layout.sidebarCollapsed"
    aria-controls="session-sidebar"
    @click="ui.layout.toggleSidebar"
  >
    <IconSymbol
      name="sidebar"
      :size="20"
    />
    <span
      v-if="pendingCount && (ui.layout.sidebarCollapsed || sidebarOverlay)"
      class="pending-count"
      :aria-label="$t('{0} 项等你处理', [pendingCount])"
    >
      {{ pendingCount }}
    </span>
  </button>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.sidebar-toggle {
  @include mixins.interactive-row;
  position: relative;
  display: grid;
  place-items: center;
  flex: none;
  width: var(--titlebar-button-size);
  height: var(--titlebar-button-size);
  max-height: 100%;
  padding: 0;
  border: var(--hairline) solid var(--transparent);
  border-radius: var(--radius-sm);
  background: var(--transparent);
  color: var(--ink-2);
  -webkit-app-region: no-drag;
}
.sidebar-toggle[aria-expanded='true'] {
  color: var(--accent);
  background: var(--accent-selected);
}
.sidebar-toggle:active {
  transform: scale(var(--press-scale));
}
.pending-count {
  display: grid;
  place-items: center;
  position: absolute;
  top: 0;
  right: 0;
  min-width: var(--pending-badge-size);
  height: var(--pending-badge-size);
  padding-inline: var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--wait-soft);
  color: var(--wait-ink);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  box-shadow: 0 0 0 var(--hairline) var(--paper);
}
</style>
