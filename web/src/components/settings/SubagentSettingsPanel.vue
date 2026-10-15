<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { api } from '@/api/client'
import { t } from '@/i18n'
import { useSettingsStore } from '@/stores/settings'
import { copySubagentSettings, emptySubagentSettings, validSubagentSettings } from '@/config/subagents'
import InkLoader from '@/components/common/InkLoader.vue'
import SubagentFields from './SubagentFields.vue'
const props = defineProps<{ loading?: boolean }>()
const emit = defineEmits<{ ready: [] }>()
const settings = useSettingsStore()
const draft = ref(copySubagentSettings(settings.subagentDefaults))
const loading = ref(false), supported = ref<boolean | null>(null), modelSelectionSupported = ref(false)
const loadError = ref(''), error = ref(''), feedback = ref('')
const configured = computed(() => Boolean(settings.subagentDefaults.calls || settings.subagentDefaults.modelIds.length))
const missingModels = computed(() => draft.value.modelIds.some((id) => !settings.modelProfiles.some((model) => model.id === id)))
const valid = computed(() => validSubagentSettings(draft.value) &&
  (!draft.value.modelIds.length || (modelSelectionSupported.value && settings.loaded && !missingModels.value)))
let disposed = false
watch(draft, () => { error.value = ''; feedback.value = '' }, { deep: true, flush: 'sync' })
async function load() {
  if (loading.value) return
  loading.value = true
  loadError.value = ''
  try {
    const capabilities = await api.getCapabilities()
    if (disposed) return
    supported.value = capabilities.subagents === true
    modelSelectionSupported.value = capabilities.subagent_model_selection === true
  } catch (reason) {
    if (!disposed) loadError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (!disposed) { loading.value = false; emit('ready') }
  }
}
function save() {
  if (props.loading || loading.value || loadError.value || !valid.value) return
  error.value = ''
  feedback.value = ''
  try {
    settings.saveSubagentDefaults(draft.value)
    feedback.value = t('已保存')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  }
}
function disableDefaults() {
  draft.value = emptySubagentSettings()
  save()
}
onMounted(load)
onScopeDispose(() => { disposed = true })
</script>

<template>
  <section class="settings-panel subagent-defaults">
    <header class="panel-heading"><h2>{{ $t('子 Agent') }}</h2></header>
    <InkLoader v-if="loading" />
    <p v-if="loadError" class="field-error" role="alert">
      {{ loadError }}
      <button type="button" class="text-button" :disabled="loading" @click="load">{{ $t('重试') }}</button>
    </p>
    <section v-else-if="supported" class="setting-group">
      <h3>{{ $t('新对话的默认配置') }}</h3>
      <p>{{ $t('新对话沿用此配置，已有对话不变。') }}</p>
      <SubagentFields v-model="draft" :model-options="settings.modelProfiles"
        :model-selection-supported="modelSelectionSupported" :disabled="loading || props.loading" />
      <p v-if="!modelSelectionSupported && draft.modelIds.length" class="field-error" role="alert">
        {{ $t('当前服务不支持指定子 Agent 模型，请取消模型选择后重试。') }}
        <button type="button" class="text-button" @click="draft.modelIds = []">{{ $t('清除模型选择') }}</button>
      </p>
      <p class="panel-copy">{{ $t('子 Agent 仅可读取和搜索。') }}</p>
      <footer class="subagent-defaults__actions">
        <button type="button" class="primary-button" :disabled="!valid || loading || props.loading" @click="save">
          {{ $t('保存默认配置') }}
        </button>
        <span v-if="feedback" class="field-saved" role="status" aria-live="polite">{{ feedback }}</span>
      </footer>
    </section>
    <section v-else-if="supported === false" class="setting-group">
      <p>{{ $t('当前服务不支持子 Agent。') }}</p>
      <button v-if="configured" type="button" class="secondary-button" :disabled="props.loading"
        @click="disableDefaults">{{ $t('关闭默认配置') }}</button>
      <span v-if="feedback" class="field-saved" role="status" aria-live="polite">{{ feedback }}</span>
    </section>
    <p v-if="error" class="field-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped lang="scss">
.subagent-defaults .subagent-fields { margin-block: var(--space-6); }
.subagent-defaults__actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-4); margin-top: var(--space-6); }
.subagent-defaults .field-saved { margin: 0; }
</style>
