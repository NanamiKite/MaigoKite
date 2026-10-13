<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ChatModelProfile, SafetyJevLayout, SafetyRequestFormat, SafetySettings } from '@/api/client'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkSelect from '@/components/common/InkSelect.vue'
import InkNumber from '@/components/common/InkNumber.vue'
import Tooltip from '@/components/common/Tooltip.vue'
import { t } from '@/i18n'

const props = defineProps<{ modelValue: SafetySettings; models: ChatModelProfile[]; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: SafetySettings] }>()
const newId = ref('')
const modelChoices = computed(() => [...new Map(props.models.map(model => [model.model, {
  value: model.model, label: `${model.name} · ${model.model}`,
}])).values()])
const formats: { id: SafetyRequestFormat; label: string }[] = [
  { id: 'jev_like', label: 'JEV / KEV' }, { id: 'laya', label: 'LAYA' },
  { id: 'openai', label: 'OpenAI 兼容' },
]
const layouts = computed(() => [
  { value: 'message', label: t('消息对象') },
  { value: 'inline', label: t('内嵌指令') },
])
const canAdd = computed(() => !props.disabled && newId.value.trim().length > 0 && newId.value.trim().length <= 100 &&
  props.modelValue.trusted_model_ids.length < 32 && !props.modelValue.trusted_model_ids.includes(newId.value.trim()))
function update(patch: Partial<SafetySettings>) { emit('update:modelValue', { ...props.modelValue, ...patch }) }
function setFormat(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'jev_like' || value === 'laya' || value === 'openai') update({ request_format: value })
}
function setLayout(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'inline' || value === 'message') update({ jev_layout: value as SafetyJevLayout })
}
function add() {
  if (!canAdd.value) return
  update({ trusted_model_ids: [...props.modelValue.trusted_model_ids, newId.value.trim()] })
  newId.value = ''
}
function remove(id: string) {
  if (!props.disabled) update({ trusted_model_ids: props.modelValue.trusted_model_ids.filter(item => item !== id) })
}
</script>

<template>
  <section class="setting-group safety-review-fields">
    <h3>{{ $t('审查规则') }}</h3>
    <div class="setting-row" data-settings-field="safety-format">
      <label for="safety-format">{{ $t('审查请求格式') }}</label>
      <InkSelect id="safety-format" :value="modelValue.request_format" :options="formats.map(format => ({ value: format.id, label: format.id === 'openai' ? $t(format.label) : format.label }))" :disabled="disabled" @change="setFormat" />
    </div>
    <div v-if="modelValue.request_format === 'jev_like'" class="setting-row" data-settings-field="safety-layout">
      <label for="safety-layout">{{ $t('JEV 布局') }}</label>
      <InkSelect id="safety-layout" :value="modelValue.jev_layout" :options="layouts" :disabled="disabled" @change="setLayout" />
    </div>
    <div class="setting-row safety-text-row" data-settings-field="safety-state">
      <label for="safety-state">{{ $t('审查状态文本') }}</label>
      <div>
        <textarea
          id="safety-state" :value="modelValue.state" rows="5" maxlength="4000" :disabled="disabled"
          :placeholder="$t('留空使用默认值')"
          @input="update({ state: ($event.target as HTMLTextAreaElement).value })"
        />
      </div>
    </div>
    <div class="setting-row" data-settings-field="safety-threshold">
      <Tooltip id="safety-threshold-help" class="safety-threshold-label" :content="$t('风险分值高于此值时需要人工确认。')">
        <label for="safety-threshold">{{ $t('风险阈值') }}</label>
        <button type="button" class="safety-threshold-help" :aria-label="$t('风险阈值说明')" aria-describedby="safety-threshold-help">
          <IconSymbol name="info" :size="14" />
        </button>
      </Tooltip>
      <InkNumber :disabled="disabled"><input
        id="safety-threshold" :value="modelValue.risk_threshold" type="number" min="0" max="100" step="1"
        required :disabled="disabled" aria-describedby="safety-threshold-help"
        @input="update({ risk_threshold: ($event.target as HTMLInputElement).valueAsNumber })"
      ></InkNumber>
    </div>
  </section>
  <section class="setting-group safety-trusted-fields" data-settings-field="safety-trusted">
    <h3>{{ $t('可信模型') }}</h3>
    <p id="safety-trusted-help">{{ $t('列表中的模型将跳过审查。') }}</p>
    <div class="safety-trusted-input">
      <input
        id="safety-trusted-input" v-model="newId" maxlength="100"
        :disabled="disabled" :placeholder="$t('选择已配置模型，或填写模型 ID')" :aria-label="$t('可信模型 ID')"
        aria-describedby="safety-trusted-help" autocomplete="off" spellcheck="false"
        @keydown.enter.stop.prevent="add"
      >
      <InkSelect
        v-if="modelChoices.length" value="" :disabled="disabled"
        :aria-label="$t('选择已配置的模型')"
        :options="[{ value: '', label: $t('已配置的模型'), disabled: true }, ...modelChoices]"
        @change="newId = ($event.target as HTMLSelectElement).value"
      />
      <button type="button" class="secondary-button" :disabled="!canAdd" @click="add">{{ $t('添加') }}</button>
    </div>
    <ul v-if="modelValue.trusted_model_ids.length" class="safety-trusted-list" :aria-label="$t('可信模型')">
      <li v-for="id in modelValue.trusted_model_ids" :key="id">
        <code>{{ id }}</code>
        <button type="button" class="icon-button" :disabled="disabled" :aria-label="$t('移除模型 {0}', [id])" @click="remove(id)">
          <IconSymbol name="close" :size="14" />
        </button>
      </li>
    </ul>
    <p v-else class="panel-copy">{{ $t('未设置可信模型。') }}</p>
    <small class="safety-trusted-count" role="status">{{ $t('已添加 {0} / 32 个模型', [modelValue.trusted_model_ids.length]) }}</small>
  </section>
</template>

<style scoped lang="scss">
.safety-review-fields textarea {
  width: 100%;
  min-width: 0;
  resize: vertical;
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}
.safety-text-row { align-items: start; }
.safety-threshold-label { justify-self: start; }
.safety-threshold-label label { font-weight: var(--weight-medium); cursor: help; }
.safety-threshold-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1);
  border: 0;
  background: var(--transparent);
  color: var(--ink-2);
  border-radius: var(--radius-sm);
  cursor: help;
}
.safety-threshold-help:hover { color: var(--accent); background: var(--control-hover); }
.safety-review-fields .panel-copy { margin-bottom: 0; margin-top: var(--space-2); }
.safety-trusted-input { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.safety-trusted-input input { flex: 1 var(--select-menu-min-width); min-width: 0; font-family: var(--font-mono); font-size: var(--text-sm); }
.safety-trusted-input > .ink-select { max-width: 100%; }
.safety-trusted-list {
  display: grid;
  gap: var(--space-2);
  list-style: none;
  padding: 0;
  margin-block: var(--space-4);
}
.safety-trusted-list li {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--paper-2);
  border-inline-start: var(--hairline) solid var(--rule);
}
.safety-trusted-list code { flex: 1; min-width: 0; overflow-wrap: anywhere; font-size: var(--text-sm); }
.safety-trusted-list button { flex: none; }
.safety-trusted-count { color: var(--ink-2); font-size: var(--text-xs); font-variant-numeric: tabular-nums; }
</style>
