<script setup lang="ts">
import type { UserMemory } from '@/api/client'
import MemoryItem from './MemoryItem.vue'
import MemoryEditor, { type MemoryDraft } from './MemoryEditor.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
defineProps<{
  title: string
  hint: string
  items: UserMemory[]
  editor?: MemoryDraft
  focusedId: string
  saving: boolean
  loading: boolean
  error: boolean
  searched: boolean
}>()
const emit = defineEmits<{
  (event: 'create'): void
  (event: 'edit', item: UserMemory): void
  (event: 'save', draft: MemoryDraft): void
  (event: 'cancel'): void
}>()
</script>
<template>
  <section
    class="memory-group"
    :aria-label="$t(title)"
  >
    <header>
      <h2>{{ $t(title) }}</h2>
      <button
        class="memory-add"
        :disabled="saving || loading || Boolean(editor)"
        :aria-label="$t('添加{0}', [$t(title)])"
        @click="emit('create')"
      >
        <IconSymbol
          name="plus"
          :size="14"
        />
        {{ $t('添加一条') }}
      </button>
    </header>
    <MemoryEditor
      v-if="editor && !editor.id"
      :draft="editor"
      :busy="saving"
      @save="emit('save', $event)"
      @cancel="emit('cancel')"
    />
    <MemoryItem
      v-for="item in items"
      :key="item.id"
      :item="item"
      :focused="focusedId === item.id"
      :editing="editor?.id === item.id"
      @edit="emit('edit', $event)"
    >
      <MemoryEditor
        v-if="editor?.id === item.id"
        :draft="editor"
        :busy="saving"
        @save="emit('save', $event)"
        @cancel="emit('cancel')"
      />
    </MemoryItem>
    <div
      v-if="!items.length && !loading && !error && !editor"
      class="memory-empty"
    >
      <svg
        viewBox="0 0 80 40"
        fill="none"
        aria-hidden="true"
      >
        <path d="M4 30C20 26 23 16 41 19S62 25 75 9M8 36h55" />
      </svg>
      <p>{{ searched ? $t('没有符合搜索的条目。') : $t(hint) }}</p>
      <button
        v-if="!searched"
        class="memory-add"
        :disabled="saving"
        @click="emit('create')"
      >
        <IconSymbol
          name="plus"
          :size="14"
        />
        {{ $t('添加一条') }}
      </button>
    </div>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.memory-group {
  min-width: 0;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-5);
  padding-bottom: var(--space-4);
  border-bottom: var(--hairline) solid var(--rule);
}
h2 {
  margin: 0;
  font-size: var(--type-subheading);
  font-weight: var(--weight-medium);
}
.memory-add {
  @include mixins.interactive-row;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  @include mixins.text-ink;
  background: var(--transparent);
  font-size: var(--type-small);
}
.memory-empty {
  padding-block: var(--space-7);
}
.memory-empty svg {
  width: var(--space-10);
  height: var(--space-8);
  stroke: var(--accent);
  stroke-width: var(--rail-stroke);
  stroke-linecap: round;
}
.memory-empty p {
  margin-block: var(--space-4);
  max-width: var(--palette-width);
  color: var(--ink-2);
  font-size: var(--type-small);
  line-height: var(--leading-reading);
}
</style>
