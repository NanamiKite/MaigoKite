<script setup lang="ts">
import { computed, type UnwrapNestedRefs } from 'vue'
import type { useModelSettings } from '@/composables/useModelSettings'
const props = defineProps<{ editor: UnwrapNestedRefs<ReturnType<typeof useModelSettings>> }>()
const models = computed(() => props.editor.modelIds)
function change(model: string, event: Event) {
  if (event.target instanceof HTMLInputElement)
    props.editor.setModelCapacity(model, event.target.value)
}
</script>
<template>
  <details
    v-if="models.length"
    class="model-context-fields"
  >
    <summary>{{ $t('窗口容量（可选）') }}</summary>
    <p>{{ $t('留空自动获取；填写容量不会改变模型服务的设置。') }}</p>
    <label
      v-for="(model, index) in models"
      :key="model"
      class="context-field"
    >
      <span>{{ model }}</span>
      <input
        :value="editor.modelCapacityInput(model)"
        type="text"
        inputmode="numeric"
        :placeholder="$t('自动获取')"
        :aria-label="$t('{0} 的窗口容量', [model])"
        :aria-invalid="Boolean(editor.capacityErrors[model])"
        :aria-describedby="
          editor.capacityErrors[model] ? `model-context-error-${index}` : undefined
        "
        @input="change(model, $event)"
      />
      <small
        v-if="editor.capacityErrors[model]"
        :id="`model-context-error-${index}`"
        role="alert"
      >
        {{ editor.capacityErrors[model] }}
      </small>
    </label>
  </details>
</template>
<style scoped lang="scss">
.model-context-fields {
  color: var(--ink-2);
  font-size: var(--text-xs);
}
summary {
  cursor: pointer;
}
p {
  margin: var(--space-3) 0;
  line-height: var(--leading-small);
}
.context-field {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.context-field span {
  overflow-wrap: anywhere;
}
input {
  width: 100%;
  padding: var(--space-3);
  color: var(--ink);
  background: var(--paper);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  font: inherit;
  font-variant-numeric: tabular-nums;
}
small {
  color: var(--seal);
}
</style>
