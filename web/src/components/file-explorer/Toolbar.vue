<script setup lang="ts">
import IconSymbol from '@/components/common/IconSymbol.vue'
defineProps<{ loading?: boolean; location?: string; opening?: boolean }>()
const emit = defineEmits<{ refresh: []; create: [type: 'file' | 'directory']; 'open-folder': [] }>()
</script>

<template>
  <div class="toolbar">
    <span :title="location">
      <IconSymbol
        name="folder"
        :size="14"
      />
      {{ location || $t('工作区文件') }}
    </span>
    <div class="toolbar-actions">
      <button
        class="icon-button"
        type="button"
        :disabled="loading"
        :aria-label="$t('新建空文件')"
        :title="$t('新建空文件')"
        @click="emit('create', 'file')"
      >
        <IconSymbol
          name="file-plus"
          :size="15"
        />
      </button>
      <button
        class="icon-button"
        type="button"
        :disabled="loading"
        :aria-label="$t('新建文件夹')"
        :title="$t('新建文件夹')"
        @click="emit('create', 'directory')"
      >
        <IconSymbol
          name="folder-plus"
          :size="15"
        />
      </button>
      <button
        class="icon-button"
        type="button"
        :disabled="opening || loading"
        :aria-busy="opening || undefined"
        :aria-label="$t('在文件资源管理器中打开文件夹')"
        :title="$t('在文件资源管理器中打开文件夹')"
        @click="emit('open-folder')"
      >
        <IconSymbol
          name="folder-open"
          :size="15"
        />
      </button>
      <button
        class="icon-button"
        type="button"
        :disabled="loading"
        :aria-busy="loading || undefined"
        :aria-label="$t('刷新文件')"
        :title="$t('刷新文件')"
        @click="emit('refresh')"
      >
        <IconSymbol
          name="refresh"
          :size="15"
        />
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.toolbar {
  display: flex;
  height: var(--measure-39px);
  flex: none;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--size-10) 0 var(--space-5);
  border-block: var(--hairline) solid var(--rule);
}
.toolbar > span {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--measure-7px);
  overflow: hidden;
  color: var(--ink-2);
  font-size: var(--text-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.toolbar-actions {
  display: flex;
  flex: none;
  gap: var(--space-1);
}
.icon-button {
  width: var(--space-8);
  height: var(--space-8);
}
</style>
