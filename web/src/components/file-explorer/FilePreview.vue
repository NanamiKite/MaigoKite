<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { FileTreeNode } from '@/composables/useFileTree'
import type { WorkspaceFileContent } from '@/api/client'
import IconSymbol from '@/components/common/IconSymbol.vue'
import CodeBlock from '@/components/common/CodeBlock.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import { desktopEffects } from '@/composables/useElectron'
import { useWorkspaceStore } from '@/stores/workspace'
import { renderHTMLFile, renderMarkdownFile, renderSVGFile } from '@/utils/file-preview'

const props = defineProps<{
  node: FileTreeNode
  content?: WorkspaceFileContent | null
  loading?: boolean
  loadError?: string | null
}>()
defineEmits<{ (event: 'retry' | 'close'): void }>()
const isElectron = Boolean(desktopEffects),
  error = ref(''),
  opening = ref(false)
const workspace = useWorkspaceStore()
const mode = ref<'source' | 'preview'>('preview')
const modes = [
  { id: 'source', label: '源码', icon: 'file' },
  { id: 'preview', label: '预览', icon: 'eye' },
] as const
const imageError = ref(false)
watch(
  () => props.content,
  () => {
    imageError.value = false
  },
)
const kind = computed(() => {
  if (/\.svg$/i.test(props.node.name) || props.content?.mime_type === 'image/svg+xml') return 'svg'
  if (props.content?.mime_type?.startsWith('image/')) return 'image'
  if (/\.(md|markdown)$/i.test(props.node.name)) return 'markdown'
  if (/\.html?$/i.test(props.node.name)) return 'html'
  return 'text'
})
const canRender = computed(() => ['markdown', 'html', 'svg'].includes(kind.value))
const svgPreview = computed(() => {
  if (
    kind.value !== 'svg' ||
    mode.value !== 'preview' ||
    !props.content ||
    props.content.binary ||
    props.content.truncated
  )
    return null
  return renderSVGFile(props.content.content)
})
const rendered = computed(() => {
  if (mode.value !== 'preview' || !props.content || props.content.binary) return ''
  if (kind.value === 'markdown') return renderMarkdownFile(props.content.content)
  if (kind.value === 'html') return renderHTMLFile(props.content.content)
  return ''
})
function navigateMode(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const index = modes.findIndex((item) => item.id === mode.value)
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? modes.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + modes.length) % modes.length
  mode.value = modes[next].id
  if (event.currentTarget instanceof HTMLElement)
    event.currentTarget.querySelectorAll<HTMLButtonElement>('button')[next]?.focus()
}
watch(
  () => [props.node.path, workspace.selectedId],
  () => {
    error.value = ''
    imageError.value = false
    mode.value = 'preview'
  },
)
async function openFile() {
  if (opening.value) return
  opening.value = true
  const path = props.node.path
  const folderId = workspace.selectedId
  try {
    const result = await desktopEffects?.openPath(path, folderId)
    if (path === props.node.path && folderId === workspace.selectedId) error.value = result || ''
  } catch (reason) {
    if (path === props.node.path && folderId === workspace.selectedId) error.value = String(reason)
  } finally {
    opening.value = false
  }
}
</script>

<template>
  <section class="file-preview">
    <header>
      <span>
        <IconSymbol
          :name="node.type === 'directory' ? 'folder' : 'file'"
          :size="16"
        />
      </span>
      <div>
        <strong>{{ node.name }}</strong>
        <small>{{ node.type === 'directory' ? $t('文件夹') : $t('只读预览') }}</small>
      </div>
      <button
        class="icon-button preview-close"
        :aria-label="$t('关闭预览')"
        :title="$t('关闭预览')"
        @click="$emit('close')"
      >
        <IconSymbol
          name="close"
          :size="16"
        />
      </button>
    </header>
    <button
      v-if="isElectron"
      class="secondary-button"
      :disabled="opening"
      :aria-busy="opening || undefined"
      @click="openFile"
    >
      {{ opening ? $t('正在打开…') : $t('在本机打开') }}
    </button>
    <p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p>
    <dl>
      <dt>{{ $t('路径') }}</dt>
      <dd>{{ node.path }}</dd>
      <template v-if="node.size != null">
        <dt>{{ $t('大小') }}</dt>
        <dd>{{ node.size }} bytes</dd>
      </template>
      <template v-if="node.modified_at">
        <dt>{{ $t('修改') }}</dt>
        <dd>{{ new Date(node.modified_at).toLocaleString($i18n.locale) }}</dd>
      </template>
    </dl>
    <div
      v-if="node.type === 'file'"
      class="content"
    >
      <InkLoader v-if="loading" />
      <p
        v-else-if="loadError"
        role="alert"
      >
        {{ loadError }}
        <button
          class="secondary-button"
          @click="$emit('retry')"
        >
          {{ $t('重试') }}
        </button>
      </p>
      <template v-else-if="kind === 'image'">
        <p v-if="content?.truncated">
          {{ $t('图片超过预览大小限制，请在本机打开。') }}
        </p>
        <p
          v-else-if="imageError"
          role="alert"
        >
          {{ $t('图片无法解码，请检查文件是否完整。') }}
        </p>
        <img
          v-else-if="content?.data_url"
          class="image-preview"
          :src="content.data_url"
          :alt="node.name"
          @error="imageError = true"
        />
      </template>
      <p v-else-if="content?.binary">
        {{ $t('二进制文件无法预览') }}
      </p>
      <p v-else-if="content && !content.content">
        {{ $t('文件没有文本内容。') }}
      </p>
      <template v-else-if="content">
        <div
          v-if="canRender"
          class="preview-modes"
          role="group"
          :aria-label="$t('文件显示方式')"
          @keydown="navigateMode"
        >
          <button
            v-for="option in modes"
            :key="option.id"
            type="button"
            :aria-pressed="mode === option.id"
            @click="mode = option.id"
          >
            <IconSymbol
              :name="option.icon"
              :size="14"
            />
            {{ $t(option.label) }}
          </button>
        </div>
        <template v-if="kind === 'svg' && mode === 'preview'">
          <p
            v-if="content.truncated"
            role="status"
          >
            {{ $t('文件过大，无法预览。请切换到源码查看。') }}
          </p>
          <p
            v-else-if="!svgPreview || imageError"
            role="status"
          >
            {{ $t('无法预览此 SVG，请查看源码。') }}
          </p>
          <div
            v-else
            class="svg-preview"
          >
            <img
              :key="svgPreview"
              class="image-preview"
              :src="svgPreview"
              :alt="node.name"
              @error="imageError = true"
            />
          </div>
        </template>
        <div
          v-else-if="kind === 'markdown' && mode === 'preview'"
          class="markdown-content"
        >
          <!-- eslint-disable vue/no-v-html -- renderMarkdownFile sanitizes HTML with DOMPurify before insertion. -->
          <div
            class="markdown-prose"
            v-html="rendered"
          />
          <!-- eslint-enable vue/no-v-html -->
        </div>
        <template v-else-if="kind === 'html' && mode === 'preview'">
          <iframe
            :key="node.path"
            :srcdoc="rendered"
            sandbox=""
            referrerpolicy="no-referrer"
            :title="node.name"
          />
        </template>
        <CodeBlock
          v-else
          :key="node.path"
          :content="content.content"
          :title="node.name"
          layout="document"
        />
      </template>
      <small v-if="content?.truncated && kind !== 'image'">
        {{ $t('文件较大，仅显示前 512 KiB。') }}
      </small>
    </div>
  </section>
</template>

<style scoped lang="scss">
@use '@/styles/markdown-content';
@use '@/styles/mixins' as mixins;

.image-preview {
  max-width: 100%;
  max-height: 65vh;
  object-fit: contain;
}
.preview-modes {
  display: flex;
  flex: none;
  gap: var(--space-1);
  border-bottom: var(--hairline) solid var(--rule);
  button {
    @include mixins.interactive-row;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--size-6);
    min-height: var(--control-row-height);
    padding: var(--space-2) var(--space-4);
    border: 0;
    border-bottom: var(--focus-width) solid var(--transparent);
    border-radius: var(--radius-sm) var(--radius-sm) 0 0;
    background: var(--transparent);
    color: var(--ink-2);
    font-size: var(--text-xs);
    font-weight: var(--weight-medium);
    &[aria-pressed='true'] {
      border-bottom-color: var(--accent);
      background: var(--accent-selected);
      color: var(--accent);
      font-weight: var(--weight-semibold);
    }
  }
}
.svg-preview {
  display: grid;
  flex: none;
  min-height: var(--explorer-preview-min-height);
  place-items: center;
  padding: var(--space-4);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  background: var(--paper);
  .image-preview {
    display: block;
  }
}
iframe {
  width: 100%;
  min-height: 360px;
  flex: 1;
  border: 1px solid var(--ink-4);
  background: white;
}
.file-preview {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  padding: var(--size-14);
  overflow: auto;
  overscroll-behavior: contain;
  background: var(--paper-2);
}
header {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--measure-9px);
}
header > span {
  display: grid;
  width: var(--measure-31px);
  height: var(--measure-31px);
  place-items: center;
  border-radius: var(--radius-md);
  color: var(--accent);
  background: var(--accent-soft);
}
header div {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.preview-close {
  flex: none;
  width: var(--space-8);
  min-height: var(--space-8);
}
header strong {
  overflow: hidden;
  font-size: var(--text-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
header small {
  margin-top: var(--space-1);
  color: var(--ink-2);
  font-size: var(--text-xs);
}
dl {
  display: grid;
  flex: none;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--measure-5px) var(--measure-9px);
  margin: var(--space-4) 0 0;
  font-size: var(--text-xs);
}
dt {
  color: var(--ink-2);
}
dd {
  margin: 0;
  overflow: hidden;
  color: var(--ink-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.content {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  gap: var(--space-3);
  margin-top: var(--space-4);
}
.file-preview > button {
  flex: none;
  align-self: flex-start;
  margin-top: var(--space-3);
}
.content > p,
.content > small {
  flex: none;
  margin-block: 0;
}
.content p,
.content small {
  color: var(--ink-2);
  font-size: var(--text-xs);
}
</style>
