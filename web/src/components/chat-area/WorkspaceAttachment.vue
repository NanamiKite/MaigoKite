<script setup lang="ts">
import type { AttachmentReference } from '@/api/images'
import { useUIStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import IconSymbol from '@/components/common/IconSymbol.vue'
import WorkspaceImage from './WorkspaceImage.vue'
const props = defineProps<{ attachment: AttachmentReference; compact?: boolean }>()
const ui = useUIStore(),
  workspace = useWorkspaceStore()
function open() {
  workspace.select(props.attachment.folder_id)
  ui.openFilePreview(props.attachment.path, props.attachment.folder_id, props.attachment.sha256)
}
</script>
<template>
  <WorkspaceImage
    v-if="attachment.type === 'image'"
    :image="attachment"
    :compact="compact"
  />
  <button
    v-else
    type="button"
    class="attachment-file"
    :class="{ compact }"
    :title="attachment.name"
    @click="open"
  >
    <IconSymbol
      name="file"
      :size="20"
    />
    <span>{{ attachment.name }}</span>
  </button>
</template>
<style scoped lang="scss">
@use '@/styles/mixins';
.attachment-file {
  @include mixins.interactive-row;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  margin: var(--space-2) 0;
  max-width: 100%;
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  color: var(--ink-2);
  font-size: var(--text-xs);
  background: var(--paper-2);
}
span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.compact {
  flex-direction: column;
  justify-content: center;
  width: 100%;
  height: var(--attachment-thumbnail-size);
  margin: 0;
  border: 0;
}
.compact span {
  width: 100%;
}
</style>
