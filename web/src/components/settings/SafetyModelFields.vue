<script setup lang="ts">
import type { SafetySettings } from '@/api/client'
import SecretField from '@/components/common/SecretField.vue'

const props = defineProps<{ modelValue: SafetySettings; apiKey: string; disabled: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: SafetySettings]
  'update:apiKey': [value: string]
  'remove-credential': []
}>()
function update(patch: Partial<SafetySettings>) {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}
</script>

<template>
  <section class="setting-group safety-model-fields">
    <h3>{{ $t('决策模型') }}</h3>
    <div
      class="setting-row"
      data-settings-field="safety-base-url"
    >
      <label for="safety-base-url">{{ $t('审查服务地址') }}</label>
      <input
        id="safety-base-url"
        :value="modelValue.decision_base_url"
        type="url"
        maxlength="2048"
        autocomplete="off"
        spellcheck="false"
        :disabled="disabled"
        :required="modelValue.enabled"
        :placeholder="$t('填写审查服务的完整地址')"
        @input="update({ decision_base_url: ($event.target as HTMLInputElement).value })"
      />
    </div>
    <div
      class="setting-row"
      data-settings-field="safety-model"
    >
      <label for="safety-model">{{ $t('决策模型 ID') }}</label>
      <input
        id="safety-model"
        :value="modelValue.decision_model"
        maxlength="200"
        autocomplete="off"
        spellcheck="false"
        :disabled="disabled"
        :required="modelValue.enabled"
        :placeholder="$t('填写审查服务中的模型 ID')"
        @input="update({ decision_model: ($event.target as HTMLInputElement).value })"
      />
    </div>
    <div
      class="setting-row"
      data-settings-field="safety-api-key"
    >
      <span>{{ $t('审查服务密钥') }}</span>
      <div class="safety-credential">
        <SecretField
          :model-value="apiKey"
          :label="$t('审查服务密钥')"
          :disabled="disabled"
          @update:model-value="emit('update:apiKey', $event)"
        />
        <div class="safety-credential-status">
          <small>
            {{ modelValue.credential_configured ? $t('已配置；留空继续使用') : $t('未配置') }}
          </small>
          <button
            v-if="modelValue.credential_configured"
            type="button"
            class="danger-button"
            :disabled="disabled"
            @click="emit('remove-credential')"
          >
            {{ $t('移除凭证') }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.safety-model-fields input {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
}
.safety-credential {
  min-width: 0;
}
.safety-credential-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-2);
}
.safety-credential-status small {
  color: var(--ink-2);
  font-size: var(--text-xs);
}
</style>
