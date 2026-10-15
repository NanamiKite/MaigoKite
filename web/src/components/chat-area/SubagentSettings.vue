<script setup lang="ts">
import { t } from '@/i18n'
import { computed, ref, watch } from 'vue'
import type { ModelOption } from '@/config/model-groups'
import { validSubagentSettings, type SubagentSettings } from '@/config/subagents'
import IconSymbol from '@/components/common/IconSymbol.vue'
import SubagentFields from '@/components/settings/SubagentFields.vue'
import StatPopover from '@/components/inkwell/StatPopover.vue'
const props = withDefaults(
  defineProps<{
    calls: number
    concurrent: number
    saving: boolean
    disabled: boolean
    modelIds?: string[]
    modelOptions?: ModelOption[]
    modelSelectionSupported?: boolean
  }>(),
  { modelIds: () => [], modelOptions: () => [], modelSelectionSupported: false },
)
const emit = defineEmits<{
  change: [value: { calls: number; concurrent: number; modelIds?: string[] }]
}>()
const draft = ref<SubagentSettings>({ calls: props.calls, concurrent: props.concurrent, modelIds: [...props.modelIds] })
const summary = computed(() =>
  t('子 Agent · {0}', [
    props.calls ? t('每次任务 {0} 个 / 并行 {1}', [props.calls, props.concurrent]) : t('关闭'),
  ]),
)
watch(
  () => [props.calls, props.concurrent, JSON.stringify(props.modelIds)],
  () => {
    draft.value = { calls: props.calls, concurrent: props.concurrent, modelIds: [...props.modelIds] }
  },
)
const valid = computed(() => validSubagentSettings(draft.value))
function apply() {
  if (valid.value && !props.disabled && !props.saving)
    emit('change', {
      calls: draft.value.calls,
      concurrent: draft.value.concurrent,
      ...(props.modelSelectionSupported ? { modelIds: [...draft.value.modelIds] } : {}),
    })
}
</script>

<template>
  <StatPopover
    :label="$t('子 Agent')"
    :title="summary"
    placement="below"
    toolbar
  >
    <template #trigger>
      <span
        class="subagent-settings__trigger"
        :class="{ enabled: props.calls > 0 }"
      ><IconSymbol
        name="subagents"
        :size="20"
      /></span>
    </template>
    <div class="subagent-settings__body">
      <SubagentFields v-model="draft" :disabled="disabled || saving" :model-options="modelOptions"
        :model-selection-supported="modelSelectionSupported" />
      <button
        type="button"
        class="secondary-button"
        :disabled="disabled || saving || !valid"
        @click="apply"
      >
        {{ saving ? $t('保存中…') : $t('应用') }}
      </button>
      <p>{{ $t('新任务生效。子 Agent 仅可读取和搜索。') }}</p>
    </div>
  </StatPopover>
</template>

<style scoped>
.subagent-settings__trigger {
  display: inline-flex;
}
.subagent-settings__trigger.enabled {
  color: var(--accent);
}
.subagent-settings__body {
  display: grid;
  gap: var(--space-4);
  justify-items: start;
}
.subagent-settings__body > :first-child {
  width: 100%;
}
p {
  margin: 0;
  line-height: var(--leading-normal);
  color: var(--ink-2);
}
</style>
