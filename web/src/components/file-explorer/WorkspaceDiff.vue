<script setup lang="ts">
import IconSymbol from '@/components/common/IconSymbol.vue'
import CodeBlock from '@/components/common/CodeBlock.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import ResizeHandle from '@/components/common/ResizeHandle.vue'
import { layoutPixels } from '@/utils/layout-media'
import { usePreviewSplit } from './usePreviewSplit'
import { useWorkspaceChanges } from './useWorkspaceChanges'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })
const {
  changes,
  selectedPath,
  selected,
  loading,
  error,
  gitAvailable,
  changesTruncated,
  diffLoading,
  diffError,
  load,
  selectChange,
} = useWorkspaceChanges(() => props.active)
const {
  root: splitRoot,
  dragging: splitDragging,
  size: listHeight,
  minimum: listMinimum,
  maximum: listMaximum,
  defaultSize: defaultListHeight,
  resize: resizeList,
  remember: rememberSplit,
} = usePreviewSplit(
  'file_explorer_changes_ratio',
  () => changes.value.length * layoutPixels('--size-30') + 2 * layoutPixels('--size-6'),
)

defineExpose({ load })
</script>

<template>
  <section class="workspace-diff">
    <header>
      <span>
        {{ $t('Git 未提交') }} · {{ changes.length }}
        <template v-if="changesTruncated">+</template>
      </span>
      <button
        class="icon-button"
        type="button"
        :disabled="loading"
        :aria-busy="loading || undefined"
        :aria-label="$t('刷新变更')"
        :title="$t('刷新变更')"
        @click="load"
      >
        <IconSymbol
          name="refresh"
          :size="14"
        />
      </button>
    </header>
    <p
      v-if="loading && !changes.length"
      class="state"
    >
      <InkLoader />
      {{ $t('正在读取 Git 变更…') }}
    </p>
    <p
      v-if="error"
      class="state error"
      role="alert"
    >
      {{ error }}
      <button
        class="secondary-button"
        @click="load"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <p
      v-if="!loading && !error && !gitAvailable"
      class="state"
    >
      {{ $t('当前工作区不是 Git 仓库。') }}
    </p>
    <p
      v-else-if="!loading && !error && !changes.length"
      class="state"
    >
      {{ $t('没有 Git 未提交变更。') }}
      <span class="state-note">{{ $t('任务中的文件改动记录保留在执行小结中。') }}</span>
    </p>
    <div
      v-if="changes.length"
      ref="splitRoot"
      class="changes-split"
      :class="{ dragging: splitDragging }"
    >
      <div
        class="change-list"
        :class="{ 'with-preview': selected }"
        :style="selected ? { flexBasis: `${listHeight}px` } : undefined"
      >
        <button
          v-for="item in changes"
          :key="item.path"
          type="button"
          :class="{ active: item.path === selectedPath }"
          @click="selectChange(item.path)"
        >
          <i :class="item.status">{{ item.status.slice(0, 1).toUpperCase() }}</i>
          <span>{{ item.path }}</span>
          <b v-if="item.additions">+{{ item.additions }}</b>
          <em v-if="item.deletions">−{{ item.deletions }}</em>
        </button>
      </div>
      <ResizeHandle
        v-if="selected"
        orientation="horizontal"
        :model-value="listHeight"
        :minimum="listMinimum"
        :maximum="listMaximum"
        :default-value="defaultListHeight"
        :label="$t('调整变更列表与预览的高度')"
        :hint="$t('拖动调整变更列表和预览；双击还原')"
        @update:model-value="resizeList"
        @change="rememberSplit"
        @dragging="splitDragging = $event"
      />
      <article
        v-if="selected"
        class="diff-preview"
        :aria-busy="diffLoading || undefined"
      >
        <header>
          <strong>{{ selected.path }}</strong>
          <small>
            {{ selected.status }}
            <template v-if="selected.diff_truncated">{{ $t('· 预览已截断') }}</template>
          </small>
        </header>
        <p v-if="diffLoading && !selected.diff">
          <InkLoader />
          {{ $t('正在生成差异…') }}
        </p>
        <p
          v-if="diffError"
          role="alert"
        >
          {{ diffError }}
          <button
            class="secondary-button"
            @click="selectChange(selectedPath)"
          >
            {{ $t('重试') }}
          </button>
        </p>
        <CodeBlock
          v-if="selected.diff"
          :key="selected.path"
          :content="selected.diff"
          kind="diff"
          :title="selected.path"
          layout="document"
        />
        <p v-else-if="!diffLoading && !diffError">
          {{ $t('该变更没有可显示的文本差异。') }}
        </p>
      </article>
    </div>
  </section>
</template>

<style scoped lang="scss">
@use '@/styles/workspace-diff';
</style>
