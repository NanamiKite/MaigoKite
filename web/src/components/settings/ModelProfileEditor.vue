<script setup lang="ts">
import { computed, onBeforeUnmount, ref, toRefs, type UnwrapNestedRefs } from 'vue'
import type { useModelSettings } from '@/composables/useModelSettings'
import { MODEL_PROVIDERS, COMPATIBLE_PROVIDER_PRESETS } from '@/config/model-providers'
import IconSymbol from '@/components/common/IconSymbol.vue'
import ModelSelectionField from './ModelSelectionField.vue'
import InkSelect from '@/components/common/InkSelect.vue'
import { t } from '@/i18n'
const props = defineProps<{ editor: UnwrapNestedRefs<ReturnType<typeof useModelSettings>> }>()
const emit = defineEmits<{ result: [ok: boolean, message: string] }>()
const copied = ref(false),
  copyError = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined
let alive = true
async function copyKey() {
  copyError.value = ''
  if (!props.editor.apiKey) return
  try {
    await navigator.clipboard.writeText(props.editor.apiKey)
    if (!alive) return
    copied.value = true
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = false
    }, 1800)
  } catch {
    copyError.value = '无法复制，请选中文字后复制。'
  }
}
async function submit() {
  settings.value.error = null
  settings.value.success = null
  await props.editor.save()
  if (props.editor.settings.error || props.editor.settings.success)
    emit(
      'result',
      !props.editor.settings.error,
      props.editor.settings.error || props.editor.settings.success || '',
    )
}
async function removeProfile() {
  settings.value.error = null
  settings.value.success = null
  await props.editor.remove()
  if (props.editor.settings.error || props.editor.settings.success)
    emit(
      'result',
      !props.editor.settings.error,
      props.editor.settings.error || props.editor.settings.success || '',
    )
}
onBeforeUnmount(() => {
  alive = false
  if (copyTimer) clearTimeout(copyTimer)
})
const {
  settings,
  draft,
  compatiblePreset,
  customService,
  provider,
  displayProviderName,
  apiKey,
  revealKey,
  endpointChanged,
  endpointHelp,
  requiresEndpoint,
  keyRequired,
  isDirty,
  canSave,
  modelCount,
  manualModelId,
  closeEditor,
  handleProviderChange,
  handlePresetChange,
  handleProtocolChange,
} = toRefs(props.editor)
const needsNewKey = computed(
  () => endpointChanged.value && keyRequired.value && !apiKey.value.trim(),
)
const protocolOptions = [
  { value: 'openai_compatible', label: 'OpenAI · Chat Completions' },
  { value: 'anthropic', label: 'Anthropic · Messages' },
]
const providerOptions = computed(() =>
  MODEL_PROVIDERS.map((item) => ({ value: item.id, label: t(item.name) })),
)
const presetOptions = computed(() => [
  { value: '', label: t('自定义地址') },
  ...COMPATIBLE_PROVIDER_PRESETS.map((item) => ({ value: item.id, label: t(item.name) })),
])
</script>

<template>
  <form
    class="profile-editor"
    @submit.prevent="submit"
  >
    <header class="editor-heading">
      <button
        type="button"
        class="back-button"
        :disabled="settings.saving"
        @click="closeEditor"
      >
        {{ $t('‹ 返回模型列表') }}
      </button>
      <div class="editor-title">
        <span
          class="provider-mark"
          :class="draft.provider"
        >
          {{ customService ? '+' : compatiblePreset?.monogram || provider.monogram }}
        </span>
        <span>
          <small>{{ draft.id ? $t('编辑模型服务') : $t('添加模型服务') }}</small>
          <h3>{{ displayProviderName }}</h3>
        </span>
        <i
          v-if="draft.isDefault"
          class="default-pill"
        >
          {{ $t('默认') }}
        </i>
      </div>
    </header>

    <fieldset
      class="editor-body"
      :disabled="settings.saving"
    >
      <label class="form-field">
        <span>
          {{ $t('服务名称') }}
          <b>{{ $t('必填') }}</b>
        </span>
        <input
          v-model.trim="draft.name"
          data-testid="profile-name"
          maxlength="80"
          :placeholder="$t('服务名称')"
        />
      </label>

      <label
        v-if="customService"
        class="form-field"
      >
        <span>{{ $t('接口协议') }}</span>
        <InkSelect
          data-testid="relay-protocol"
          :value="draft.provider"
          :disabled="settings.saving"
          :options="protocolOptions"
          @change="handleProtocolChange"
        />
      </label>

      <div
        class="form-field"
        data-settings-field="model-base-url"
      >
        <label for="model-base-url">
          {{ customService ? $t('中转站 / 网关地址') : $t(provider.baseUrlLabel) }}
          <b v-if="requiresEndpoint">{{ $t('必填') }}</b>
        </label>
        <input
          id="model-base-url"
          v-model.trim="draft.baseUrl"
          data-testid="base-url"
          type="url"
          :required="requiresEndpoint"
          :placeholder="
            customService
              ? draft.provider === 'anthropic'
                ? 'https://gateway.example'
                : 'https://gateway.example/v1'
              : compatiblePreset?.baseUrl || $t(provider.baseUrlPlaceholder)
          "
        />
        <small>
          {{ endpointHelp }}
          <a
            v-if="compatiblePreset?.docsUrl && !customService"
            :href="compatiblePreset.docsUrl"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ $t('查看服务文档 ↗') }}
          </a>
        </small>
      </div>

      <label
        v-if="provider.apiKeyMode !== 'none'"
        class="form-field"
        data-settings-field="model-api-key"
      >
        <span>
          API Key
          <b>{{ keyRequired ? $t('必填') : $t('按需') }}</b>
        </span>
        <span class="secret">
          <input
            v-model="apiKey"
            data-testid="api-key"
            :type="revealKey ? 'text' : 'password'"
            autocomplete="new-password"
            :aria-describedby="needsNewKey ? 'model-key-warning' : undefined"
            :placeholder="
              draft.credentialConfigured && !endpointChanged
                ? $t('已配置；留空继续使用')
                : $t('粘贴 API Key')
            "
          />
          <button
            type="button"
            :aria-label="revealKey ? $t('隐藏 API Key') : $t('显示 API Key')"
            @click="revealKey = !revealKey"
          >
            <IconSymbol
              :name="revealKey ? 'eye-off' : 'eye'"
              :size="16"
            />
          </button>
          <button
            type="button"
            :disabled="!apiKey"
            :aria-label="$t('复制 API Key')"
            @click="copyKey"
          >
            <IconSymbol
              :name="copied ? 'check' : 'copy'"
              :size="16"
            />
          </button>
        </span>
        <small
          v-if="copied"
          role="status"
        >
          {{ $t('已复制') }}
        </small>
        <small
          v-if="copyError"
          role="alert"
        >
          {{ $t(copyError) }}
        </small>
        <small
          v-if="needsNewKey"
          id="model-key-warning"
          class="warning"
          role="status"
        >
          {{ $t('服务地址或接口已更换，请重新填写 API Key。') }}
        </small>
        <small
          v-if="draft.credentialConfigured && !endpointChanged"
          class="credential-confirmed"
        >
          <i />
          {{ $t('密钥已保存') }}
        </small>
      </label>

      <ModelSelectionField :editor="editor" />

      <details
        v-if="!customService || draft.provider === 'openai_compatible'"
        class="custom-settings"
      >
        <summary>
          <span>
            <strong>{{ $t('自定义设置') }}</strong>
          </span>
          <i>›</i>
        </summary>
        <div class="custom-settings-body">
          <label
            v-if="!customService"
            class="form-field"
          >
            <span>{{ $t('接口类型') }}</span>
            <InkSelect
              data-testid="provider-type"
              :value="draft.provider"
              :disabled="settings.saving"
              :options="providerOptions"
              @change="handleProviderChange"
            />
          </label>
          <label
            v-if="draft.provider === 'openai_compatible'"
            class="form-field"
          >
            <span>{{ $t('服务预设') }}</span>
            <InkSelect
              data-testid="preset-select"
              :value="compatiblePreset?.id || ''"
              :disabled="settings.saving"
              :options="presetOptions"
              @change="handlePresetChange"
            />
          </label>
        </div>
      </details>

      <p
        v-if="settings.validation"
        class="validation"
        :class="{ ok: settings.validation.ok }"
      >
        <IconSymbol
          :name="settings.validation.ok ? 'check' : 'alert'"
          :size="15"
        />
        {{ settings.validation.message }}
      </p>
    </fieldset>

    <footer class="editor-footer">
      <div>
        <button
          v-if="draft.id"
          type="button"
          class="danger-button"
          :disabled="settings.saving"
          @click="removeProfile"
        >
          {{ $t('删除服务') }}
        </button>
      </div>
      <div class="save-actions">
        <span>{{ isDirty ? $t('有未保存的修改') : $t('已保存') }}</span>
        <button
          type="button"
          class="secondary-button"
          :disabled="!canSave || settings.validating || settings.saving"
          :aria-busy="settings.validating || undefined"
          @click="
            settings.validateChatModel(
              { ...draft, model: draft.model || manualModelId.trim() },
              apiKey.trim(),
            )
          "
        >
          {{
            settings.validating
              ? $t('测试中…')
              : modelCount > 1
                ? $t('测试首个模型')
                : $t('测试连接')
          }}
        </button>
        <button
          type="submit"
          class="primary-button"
          :disabled="!canSave || settings.saving"
          :aria-busy="settings.saving || undefined"
        >
          {{
            settings.saving
              ? $t('保存中…')
              : modelCount > 1
                ? $t('保存 {0} 个模型', [modelCount])
                : $t('保存配置')
          }}
        </button>
      </div>
    </footer>
  </form>
</template>
<style scoped lang="scss">
@use '@/styles/model-editor';
</style>
