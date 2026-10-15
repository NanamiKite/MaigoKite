<script setup lang="ts">
import type { ModelOption, ModelServiceGroup } from '@/config/model-groups'
import IconSymbol from '@/components/common/IconSymbol.vue'

defineProps<{
  group: ModelServiceGroup<ModelOption>
  menuId: string
  providerId: string
  expanded: boolean
  modelValue: string
  activeId: string
  keyboardNavigation: boolean
}>()
defineEmits<{ toggle: []; highlight: [id: string]; choose: [id: string] }>()
</script>

<template>
  <section
    :id="providerId"
    class="model-group"
    role="treeitem"
    aria-level="1"
    :aria-label="group.name"
    :aria-expanded="expanded"
  >
    <button
      :id="`${providerId}-heading`"
      type="button"
      class="model-group-toggle"
      :data-node-id="providerId"
      :aria-label="$t('展开或收起 {0} 的模型', [group.name])"
      :aria-expanded="expanded"
      :aria-controls="expanded ? `${providerId}-models` : undefined"
      :data-highlighted="keyboardNavigation && activeId === providerId"
      @focus="$emit('highlight', providerId)"
      @click="$emit('toggle')"
    >
      <IconSymbol
        class="group-chevron"
        name="chevron"
        :size="12"
      />
      <span class="model-group-copy">
        <strong>{{ group.name }}</strong>
        <small v-if="group.providerName !== group.name">{{ $t(group.providerName) }}</small>
      </span>
      <IconSymbol
        v-if="!expanded && group.models.some((item) => item.id === modelValue)"
        class="model-group-current"
        name="check"
        :size="14"
      />
      <span class="model-group-count">{{ $t('{0} 个模型', [group.models.length]) }}</span>
    </button>
    <div
      v-if="expanded"
      :id="`${providerId}-models`"
      role="group"
    >
      <button
        v-for="item in group.models"
        :id="`${menuId}-${item.id}`"
        :key="item.id"
        type="button"
        role="treeitem"
        aria-level="2"
        tabindex="-1"
        class="model-option"
        :data-node-id="item.id"
        :title="item.model"
        :aria-selected="item.id === modelValue"
        :data-highlighted="keyboardNavigation && item.id === activeId"
        @pointermove="$emit('highlight', item.id)"
        @focus="$emit('highlight', item.id)"
        @click="$emit('choose', item.id)"
      >
        <span>{{ item.model }}</span>
        <small v-if="item.isDefault">{{ $t('默认') }}</small>
        <IconSymbol
          v-if="item.id === modelValue"
          name="check"
          :size="15"
        />
      </button>
    </div>
  </section>
</template>

<style scoped lang="scss">
@use '@/styles/model-picker-group';
</style>
