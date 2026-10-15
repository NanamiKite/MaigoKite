<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/i18n'
import type { ModelOption } from '@/config/model-groups'
import { validSubagentSettings, type SubagentSettings } from '@/config/subagents'
import InkNumber from '@/components/common/InkNumber.vue'
const props = withDefaults(
  defineProps<{
    modelValue: SubagentSettings
    modelOptions: ModelOption[]
    modelSelectionSupported: boolean
    disabled?: boolean
  }>(),
  { disabled: false },
)
const emit = defineEmits<{ 'update:modelValue': [value: SubagentSettings] }>()
function update(value: Partial<SubagentSettings>) {
  emit('update:modelValue', { ...props.modelValue, ...value })
}
const calls = computed({
  get: () => props.modelValue.calls,
  set: (value: number) => update({ calls: value }),
})
const concurrent = computed({
  get: () => props.modelValue.concurrent,
  set: (value: number) => update({ concurrent: value }),
})
const selectedModels = computed({
  get: () => props.modelValue.modelIds,
  set: (value: string[]) => update({ modelIds: value }),
})
const modelChoices = computed(() => [
  ...props.modelOptions,
  ...selectedModels.value
    .filter((id) => !props.modelOptions.some((model) => model.id === id))
    .map((id) => ({ id, name: t('配置已不存在，请取消选择'), model: id })),
])
const valid = computed(() => validSubagentSettings(props.modelValue))
</script>

<template>
  <div class="subagent-fields">
    <label
      class="subagent-fields__row"
      data-settings-field="subagent-calls"
    >
      <span>{{ $t('每次任务最多派发（0 关闭）') }}</span>
      <InkNumber :disabled="disabled">
        <input
          v-model.number="calls"
          type="number"
          min="0"
          max="32"
          step="1"
          :disabled="disabled"
        />
      </InkNumber>
    </label>
    <label
      class="subagent-fields__row"
      data-settings-field="subagent-concurrent"
    >
      <span>{{ $t('最多同时运行') }}</span>
      <InkNumber :disabled="disabled || calls === 0">
        <input
          v-model.number="concurrent"
          type="number"
          min="1"
          :max="calls ? Math.min(8, calls) : 8"
          step="1"
          :disabled="disabled || calls === 0"
        />
      </InkNumber>
    </label>
    <fieldset
      v-if="modelSelectionSupported"
      class="subagent-fields__models"
      :disabled="disabled"
      data-settings-field="subagent-models"
    >
      <legend>{{ $t('允许指定的其他模型') }}</legend>
      <div class="subagent-fields__choices">
        <label
          v-for="model in modelChoices"
          :key="model.id"
          :title="model.id"
        >
          <input
            v-model="selectedModels"
            type="checkbox"
            :value="model.id"
          />
          <span>{{ model.name || model.model }} · {{ model.model }}</span>
        </label>
        <p v-if="!modelChoices.length">
          {{ $t('请先在模型设置中添加模型。') }}
        </p>
      </div>
      <p>{{ $t('默认跟随主模型。勾选后可在聊天中指定；任务材料会发送给所选模型服务。') }}</p>
    </fieldset>
    <p
      v-if="!valid"
      class="field-error"
      role="alert"
    >
      {{
        $t('每次任务限 0–32 个，并行限 1–8 个且不能大于任务派发上限；允许指定的模型最多 32 个。')
      }}
    </p>
  </div>
</template>

<style scoped lang="scss">
.subagent-fields {
  display: grid;
  gap: var(--space-5);
  min-width: 0;
}
.subagent-fields__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-4);
}
.subagent-fields__row .ink-number {
  flex: none;
  width: calc(var(--measure-58px) + var(--size-28) * 2);
}
.subagent-fields__models {
  min-width: 0;
  margin: 0;
  padding: var(--space-4);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
}
legend {
  padding-inline: var(--space-2);
  color: var(--ink);
}
.subagent-fields__choices {
  max-height: var(--measure-200px);
  overflow: auto;
  overscroll-behavior: contain;
}
.subagent-fields__choices label {
  display: flex;
  gap: var(--space-3);
  align-items: start;
  margin-block: var(--space-3);
}
.subagent-fields__choices span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.subagent-fields__choices input {
  flex: none;
  width: var(--size-18);
  height: var(--size-18);
  accent-color: var(--accent);
}
p {
  margin: var(--space-3) 0 0;
  line-height: var(--leading-normal);
  color: var(--ink-2);
}
</style>
