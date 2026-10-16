<script setup lang="ts">
import type { FileTreeNode } from '@/composables/useFileTree'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'

const props = defineProps<{ node: FileTreeNode; depth: number; selectedPath?: string }>()
const emit = defineEmits<{ (event: 'expand' | 'select', node: FileTreeNode): void }>()

function select() {
  emit('select', props.node)
  if (props.node.type === 'directory') {
    emit('expand', props.node)
  }
}
</script>

<template>
  <div class="file-node">
    <button
      type="button"
      role="treeitem"
      :data-tree-item="node.path"
      :aria-level="depth + 1"
      :aria-selected="node.path === selectedPath"
      :tabindex="node.path === selectedPath || (depth === 0 && !selectedPath) ? 0 : -1"
      :class="{ selected: node.path === selectedPath }"
      :title="node.path"
      :aria-expanded="node.type === 'directory' ? Boolean(node.expanded) : undefined"
      :aria-busy="node.loading || undefined"
      :style="{ '--tree-depth': depth }"
      @click="select"
    >
      <IconSymbol
        v-if="node.type === 'directory'"
        name="chevron"
        :size="12"
        class="chevron"
        :class="{ expanded: node.expanded }"
      />
      <span
        v-else
        class="spacer"
      />
      <IconSymbol
        :name="node.type === 'directory' ? 'folder' : 'file'"
        :size="15"
      />
      <span>{{ node.name }}</span>
    </button>
    <div
      v-if="node.type === 'directory' && node.expanded"
      role="group"
      class="tree-children"
    >
      <InkLoader v-if="node.loading" />
      <p
        v-else-if="node.loaded && !node.children?.length"
        class="empty-copy"
      >
        {{ $t('空文件夹') }}
      </p>
      <FileNode
        v-for="child in node.children || []"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        :selected-path="selectedPath"
        @expand="emit('expand', $event)"
        @select="emit('select', $event)"
      />
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.file-node button {
  @include mixins.interactive-row;
  display: flex;
  width: 100%;
  height: var(--space-8);
  align-items: center;
  gap: var(--size-6);
  padding-right: var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--ink-2);
  background: var(--transparent);
  cursor: pointer;
  text-align: left;
  padding-inline-start: calc(var(--space-3) + var(--tree-depth) * var(--tree-indent));
}
.tree-children {
  border-inline-start: var(--hairline) solid var(--rule);
}
.file-node button.selected {
  background: var(--accent-selected);
  @include mixins.text-ink;
}
.file-node button span:last-child {
  overflow: hidden;
  font-size: var(--text-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chevron {
  transition: transform var(--motion-fast) var(--ease);
}
.chevron.expanded {
  transform: rotate(var(--measure-90deg));
}
.spacer {
  width: var(--space-4);
  flex: none;
}
</style>
