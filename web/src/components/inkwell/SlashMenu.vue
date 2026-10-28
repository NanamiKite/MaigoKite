<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'
export interface SlashCommand {
  id: string
  label: string
  icon?: string
  group?: '添加' | '操作' | '设置' | '技能'
  keywords?: string
  disabled?: boolean
  reason?: string
}
const props = withDefaults(
  defineProps<{
    items: SlashCommand[]
    selected: number
    id?: string
    searchable?: boolean
    query?: string
    loading?: boolean
    error?: string
  }>(),
  { id: 'inkwell-commands', query: '', error: '' },
)
const emit = defineEmits<{
  select: [item: SlashCommand]
  'update:query': [value: string]
  highlight: [index: number]
  retry: []
}>()
const menu = ref<HTMLElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
watch(
  () => props.searchable,
  async (open) => {
    if (open) {
      await nextTick()
      search.value?.focus({ preventScroll: true })
    }
  },
  { immediate: true },
)
watch(
  () => [props.selected, props.items.length],
  async () => {
    await nextTick()
    menu.value
      ?.querySelector<HTMLElement>('[aria-selected="true"]')
      ?.scrollIntoView?.({ block: 'nearest' })
  },
  { immediate: true, flush: 'post' },
)
</script>
<template>
  <div class="slash-menu">
    <input
      v-if="searchable"
      ref="search"
      class="command-search"
      type="search"
      :value="query"
      :placeholder="$t('搜索指令…')"
      :aria-label="$t('搜索指令')"
      role="combobox"
      :aria-controls="id"
      aria-expanded="true"
      :aria-activedescendant="items[selected] ? id + '-option-' + selected : undefined"
      @input="emit('update:query', ($event.target as HTMLInputElement).value)"
    />
    <div
      :id="id"
      ref="menu"
      class="command-results"
      role="listbox"
      :aria-label="$t('添加与操作')"
    >
      <template
        v-for="(item, index) in items"
        :key="item.id"
      >
        <div
          v-if="index === 0 || item.group !== items[index - 1].group"
          class="command-group"
          role="presentation"
        >
          {{ $t(item.group || '操作') }}
        </div>
        <button
          :id="id + '-option-' + index"
          type="button"
          role="option"
          tabindex="-1"
          :aria-selected="index === selected"
          :aria-disabled="item.disabled || undefined"
          :title="item.reason"
          @pointermove="emit('highlight', index)"
          @mousedown.prevent
          @click="!item.disabled && emit('select', item)"
        >
          <IconSymbol
            v-if="item.icon"
            :name="item.icon"
            :size="16"
          />
          <span class="command-copy">
            <span>{{ item.group === '技能' ? item.label : $t(item.label) }}</span>
            <small v-if="item.disabled && item.reason">{{ item.reason }}</small>
          </span>
          <code :title="'/' + item.id">/{{ item.id }}</code>
        </button>
      </template>
    </div>
    <p
      v-if="!items.length && !loading"
      role="status"
    >
      {{ $t('没有找到相关内容。') }}
    </p>
    <p
      v-if="loading"
      class="command-status"
      role="status"
    >
      <InkLoader />
      {{ $t('正在读取技能…') }}
    </p>
    <p
      v-else-if="error"
      class="command-status"
      role="alert"
    >
      {{ error }}
      <button
        type="button"
        @click="emit('retry')"
      >
        {{ $t('重试') }}
      </button>
    </p>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/composer-menu';
</style>
