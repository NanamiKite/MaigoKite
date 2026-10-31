<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useLayoutStore } from '@/stores/layout'
import { useWorkspaceStore } from '@/stores/workspace'
import { useFileTree } from '@/composables/useFileTree'
import IconSymbol from '@/components/common/IconSymbol.vue'
import FileTree from '../file-explorer/FileTree.vue'
import FilePreview from '../file-explorer/FilePreview.vue'
import FileActions from '../file-explorer/FileActions.vue'
import { useUIStore } from '@/stores/ui'
import type { WorkspaceFileEntry } from '@/api/client'
import WorkspaceDiff from '../file-explorer/WorkspaceDiff.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import ResizeHandle from '@/components/common/ResizeHandle.vue'
import { usePreviewSplit } from '../file-explorer/usePreviewSplit'

const layoutStore = useLayoutStore()
const workspace = useWorkspaceStore()
const ui = useUIStore()
const explorer = ref<HTMLElement | null>(null)
withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })
const {
  tree,
  selectedNode,
  selectedContent,
  loading,
  previewLoading,
  previewError,
  error,
  loadFileTree,
  toggleExpand,
  selectNode,
  revealFile,
  closePreview,
} = useFileTree()
const activeTab = ref<'files' | 'changes'>('files')
const {
  root: splitRoot,
  dragging: splitDragging,
  size: treeHeight,
  minimum: treeMinimum,
  maximum: treeMaximum,
  defaultSize: defaultTreeHeight,
  resize: resizeTree,
  remember: rememberSplit,
} = usePreviewSplit('file_explorer_tree_ratio')
const creationParent = computed(() =>
  selectedNode.value?.type === 'directory'
    ? selectedNode.value.path
    : selectedNode.value?.path.split('/').slice(0, -1).join('/') || '',
)
let shownRequest = 0
watch(
  () => [ui.filePreviewRequest, loading.value] as const,
  async ([request, treeLoading]) => {
    if (
      !request ||
      treeLoading ||
      request.serial === shownRequest ||
      request.folderId !== workspace.selectedId
    )
      return
    shownRequest = request.serial
    activeTab.value = 'files'
    await revealFile(request.path, 'file', request.sha256)
    await nextTick()
    if (
      ui.filePreviewRequest?.serial !== request.serial ||
      request.folderId !== workspace.selectedId
    )
      return
    Array.from(explorer.value?.querySelectorAll<HTMLElement>('[data-tree-item]') || [])
      .find((row) => row.dataset.treeItem === request.path)
      ?.scrollIntoView({ block: 'nearest' })
    explorer.value
      ?.querySelector<HTMLButtonElement>('.preview-close')
      ?.focus({ preventScroll: true })
  },
  { immediate: true, flush: 'post' },
)
async function created(entry: WorkspaceFileEntry) {
  const folderId = workspace.selectedId
  await loadFileTree('')
  if (folderId === workspace.selectedId) await revealFile(entry.path, entry.type)
}
</script>

<template>
  <div
    ref="explorer"
    class="file-explorer"
    :class="{ embedded, collapsed: !embedded && layoutStore.fileExplorerCollapsed }"
  >
    <button
      v-if="!embedded && layoutStore.fileExplorerCollapsed"
      class="collapsed-button icon-button"
      type="button"
      :title="$t('展开文件栏')"
      :aria-label="$t('展开文件栏')"
      @click="layoutStore.toggleFileExplorer"
    >
      <IconSymbol
        name="files"
        :size="17"
      />
    </button>

    <template v-else>
      <header
        v-if="!embedded"
        class="explorer-header"
      >
        <div>
          <p :title="workspace.current?.root_path">
            {{ workspace.current?.name || $t('工作区') }}
          </p>
          <h2>{{ $t('文件') }}</h2>
        </div>
        <button
          class="icon-button"
          type="button"
          :aria-label="$t('收起文件栏')"
          @click="layoutStore.toggleFileExplorer"
        >
          <IconSymbol
            name="chevron"
            :size="16"
          />
        </button>
      </header>
      <nav
        v-if="!embedded"
        class="explorer-tabs"
      >
        <button
          type="button"
          :class="{ active: activeTab === 'files' }"
          @click="activeTab = 'files'"
        >
          {{ $t('文件') }}
        </button>
        <button
          type="button"
          :class="{ active: activeTab === 'changes' }"
          @click="activeTab = 'changes'"
        >
          {{ $t('变更') }}
        </button>
      </nav>
      <template v-if="embedded || activeTab === 'files'">
        <FileActions
          :loading="loading"
          :parent="creationParent"
          @refresh="loadFileTree('')"
          @created="created"
        />
        <p
          v-if="error && tree.length"
          class="empty-copy"
          role="alert"
        >
          {{ error }}
          <button
            class="secondary-button"
            :disabled="loading"
            @click="loadFileTree('')"
          >
            {{ $t('重试') }}
          </button>
        </p>
        <div
          ref="splitRoot"
          class="explorer-split"
          :class="{ dragging: splitDragging }"
        >
          <div
            class="explorer-body"
            :class="{ 'with-preview': selectedNode }"
            :style="selectedNode ? { flexBasis: `${treeHeight}px` } : undefined"
          >
            <FileTree
              v-if="tree.length"
              :nodes="tree"
              :selected-path="selectedNode?.path"
              @expand="toggleExpand"
              @select="selectNode"
            />
            <div
              v-else
              class="empty-files"
            >
              <InkLoader v-if="loading" />
              <span
                v-else
                class="empty-icon"
              >
                <IconSymbol
                  name="folder"
                  :size="24"
                />
              </span>
              <strong>{{ loading ? $t('正在读取工作区…') : $t('没有可显示的文件') }}</strong>
              <p
                v-if="error"
                role="alert"
              >
                {{ error }}
              </p>
              <button
                v-if="error"
                class="secondary-button"
                :disabled="loading"
                @click="loadFileTree('')"
              >
                {{ $t('重试') }}
              </button>
              <p v-else-if="!loading">
                {{ $t('选择工作区文件夹，文件会显示在这里。') }}
              </p>
            </div>
          </div>
          <ResizeHandle
            v-if="selectedNode"
            orientation="horizontal"
            :model-value="treeHeight"
            :minimum="treeMinimum"
            :maximum="treeMaximum"
            :default-value="defaultTreeHeight"
            @update:model-value="resizeTree"
            @change="rememberSplit"
            @dragging="splitDragging = $event"
          />
          <FilePreview
            v-if="selectedNode"
            :node="selectedNode"
            :content="selectedContent"
            :loading="previewLoading"
            :load-error="previewError"
            @retry="selectedNode && selectNode(selectedNode)"
            @close="closePreview"
          />
        </div>
      </template>
      <WorkspaceDiff v-else />
      <footer v-if="!embedded">
        <span>{{ $t('工作区文件') }}</span>
      </footer>
    </template>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/file-explorer';
</style>
