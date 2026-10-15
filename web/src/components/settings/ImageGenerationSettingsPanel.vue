<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  getImageSettings,
  saveImageSettings,
  type ImageGenerationSettings,
} from '@/api/image-generation'
import CollapseCard from '@/components/common/CollapseCard.vue'
import InkSelect from '@/components/common/InkSelect.vue'
import SecretField from '@/components/common/SecretField.vue'
import { t } from '@/i18n'

const emit = defineEmits<{ saved: [enabled: boolean] }>()
const route = useRoute()
const draft = ref<ImageGenerationSettings | null>(null)
const key = ref(''),
  clear = ref(false),
  configured = ref(false)
const busy = ref(false),
  error = ref(''),
  feedback = ref('')
const controller = new AbortController()
let disposed = false
const protocols = [
  { value: 'images', label: 'OpenAI Images / OpenAI-compatible' },
  { value: 'responses', label: 'OpenAI Responses' },
]
function setProtocol(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (draft.value && (value === 'images' || value === 'responses')) draft.value.protocol = value
}
function setResponseFormat(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (draft.value && (value === '' || value === 'b64_json' || value === 'url')) {
    draft.value.response_format = value
  }
}
watch(
  () => draft.value?.protocol,
  (value) => {
    if (value === 'responses' && draft.value) draft.value.response_format = ''
  },
)
async function load() {
  busy.value = true
  error.value = ''
  try {
    const response = await getImageSettings(controller.signal)
    if (disposed) return
    draft.value = response.settings
    configured.value = response.credential_configured
  } catch (reason) {
    if (!disposed) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (!disposed) busy.value = false
  }
}
async function save() {
  if (!draft.value || busy.value) return
  busy.value = true
  error.value = feedback.value = ''
  try {
    const response = await saveImageSettings({ ...draft.value }, key.value, clear.value)
    if (disposed) return
    draft.value = response.settings
    configured.value = response.credential_configured
    key.value = ''
    clear.value = false
    feedback.value = t('已保存')
    emit('saved', response.settings.enabled)
  } catch (reason) {
    if (!disposed) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (!disposed) busy.value = false
  }
}
onMounted(() => {
  void load()
})
onBeforeUnmount(() => {
  disposed = true
  controller.abort()
  key.value = ''
})
</script>
<template>
  <CollapseCard
    variant="settings"
    class="settings-panel"
    data-settings-field="image-generation"
    :title="$t('图片生成')"
    icon="files"
    :default-expanded="route.query.field === 'image-generation'"
  >
    <p
      v-if="error"
      class="field-error"
      role="alert"
    >
      {{ error }}
      <button
        v-if="!draft"
        type="button"
        :disabled="busy"
        @click="load"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <form
      v-if="draft"
      class="image-settings"
      @submit.prevent="save"
    >
      <fieldset :disabled="busy">
        <label class="toggle">
          <input
            v-model="draft.enabled"
            type="checkbox"
          />
          {{ $t('启用图片生成') }}
        </label>
        <div class="select-field">
          <label for="image-protocol">{{ $t('调用接口') }}</label>
          <InkSelect
            id="image-protocol"
            :value="draft.protocol"
            :options="protocols"
            :disabled="busy"
            :aria-label="$t('调用接口')"
            @change="setProtocol"
          />
        </div>
        <label>
          {{ $t('服务地址') }}
          <input
            v-model="draft.base_url"
            type="url"
            required
            maxlength="2000"
            placeholder="https://api.openai.com/v1"
          />
        </label>
        <label>
          {{ $t('模型 ID') }}
          <input
            v-model="draft.model"
            :required="draft.enabled"
            maxlength="200"
            autocomplete="off"
          />
        </label>
        <SecretField
          v-model="key"
          :label="$t('API Key')"
          :disabled="clear"
        />
        <small>
          {{
            configured
              ? $t('已配置密钥；留空保留。修改服务地址后请重新填写。')
              : $t('无密钥的本地服务可留空。')
          }}
        </small>
        <label
          v-if="configured"
          class="toggle"
        >
          <input
            v-model="clear"
            type="checkbox"
            :disabled="!!key"
          />
          {{ $t('保存时移除密钥') }}
        </label>
        <details>
          <summary>{{ $t('可选输出设置') }}</summary>
          <label>
            {{ $t('图片尺寸') }}
            <input
              v-model="draft.size"
              maxlength="32"
              placeholder="auto / 1024x1024"
            />
          </label>
          <small>{{ $t('留空使用默认尺寸。') }}</small>
          <div
            v-if="draft.protocol === 'images'"
            class="select-field"
          >
            <label for="image-response-format">{{ $t('返回格式') }}</label>
            <InkSelect
              id="image-response-format"
              :value="draft.response_format"
              :options="[
                { value: '', label: $t('由服务决定') },
                { value: 'b64_json', label: 'Base64' },
                { value: 'url', label: 'URL' },
              ]"
              :disabled="busy"
              :aria-label="$t('返回格式')"
              @change="setResponseFormat"
            />
          </div>
          <label>
            {{ $t('超时（秒）') }}
            <input
              v-model.number="draft.timeout_seconds"
              type="number"
              min="10"
              max="600"
              required
            />
          </label>
        </details>
        <p class="panel-copy">
          {{ $t('图片生成可能产生费用。') }}
        </p>
        <footer class="settings-actions">
          <span role="status">{{ feedback }}</span>
          <button
            class="primary-button"
            type="submit"
          >
            {{ busy ? $t('正在保存…') : $t('保存') }}
          </button>
        </footer>
      </fieldset>
    </form>
  </CollapseCard>
</template>
<style scoped lang="scss">
.image-settings {
  fieldset {
    border: 0;
    padding: 0;
    display: grid;
    gap: var(--space-3);
    min-width: 0;
  }
  label:not(.toggle),
  .select-field {
    display: grid;
    gap: var(--space-2);
  }
  .toggle {
    display: flex;
    gap: var(--space-2);
    align-items: center;
  }
  input:not([type='checkbox']) {
    width: 100%;
    padding: var(--space-2);
  }
  small {
    color: var(--ink-3);
  }
  details > label,
  details > .select-field {
    margin-top: var(--space-3);
  }
}
</style>
