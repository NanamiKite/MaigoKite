<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { api, type SafetySettings, type SafetySettingsUpdate } from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'
import InkLoader from '@/components/common/InkLoader.vue'
import SafetyModelFields from './SafetyModelFields.vue'
import SafetyReviewFields from './SafetyReviewFields.vue'
import { confirmAction } from '@/utils/confirm-action'

const props = defineProps<{ loading?: boolean }>()
const emit = defineEmits<{ ready: [] }>()
const settings = useSettingsStore()
const draft = ref<SafetySettings | null>(null), confirmed = ref<SafetySettings | null>(null)
const apiKey = ref(''), error = ref(''), feedback = ref(''), unavailable = ref(false)
const action = ref<'load' | 'save' | 'credential' | ''>('')
const failedAction = ref<'load' | 'save' | 'credential'>('load')
let controller: AbortController | undefined
const busy = computed(() => !!action.value || !!props.loading)
function configuration(value: SafetySettings): SafetySettingsUpdate {
  return {
    enabled: value.enabled, request_format: value.request_format, jev_layout: value.jev_layout,
    state: value.state,
    decision_base_url: value.decision_base_url.trim(), decision_model: value.decision_model.trim(),
    trusted_model_ids: [...new Set(value.trusted_model_ids.map(id => id.trim()).filter(Boolean))],
    risk_threshold: value.risk_threshold,
  }
}
const dirty = computed(() => !!draft.value && !!confirmed.value && (
  !!apiKey.value || JSON.stringify(configuration(draft.value)) !== JSON.stringify(configuration(confirmed.value))
))
function apply(value: SafetySettings) {
  // Never retain a secret in the saved projection, even if a server echoes one.
  const publicValue = { ...configuration(value), credential_configured: value.credential_configured }
  confirmed.value = publicValue
  draft.value = { ...publicValue, trusted_model_ids: [...publicValue.trusted_model_ids] }
}
function begin(kind: typeof failedAction.value) {
  controller?.abort()
  const request = new AbortController()
  controller = request
  action.value = kind
  failedAction.value = kind
  error.value = ''
  feedback.value = ''
  return request
}
async function load() {
  if (action.value) return
  if (dirty.value && !await confirmAction(t('重新读取会放弃未保存的安全设置。继续？'), false)) return
  if (action.value) return
  const request = begin('load')
  unavailable.value = false
  try {
    const capabilities = await api.getCapabilities(request.signal)
    if (request.signal.aborted) return
    if (!capabilities.safety_review) {
      unavailable.value = true
      return
    }
    const value = await api.getSafetySettings(request.signal)
    if (!request.signal.aborted) { apply(value); apiKey.value = '' }
  } catch (reason) {
    if (!request.signal.aborted) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (!request.signal.aborted) { action.value = ''; emit('ready') }
  }
}
function validate(value: SafetySettingsUpdate): string {
  if (value.enabled && (!value.decision_base_url || !value.decision_model))
    return t('启用审查需要填写服务地址和决策模型。')
  if (value.decision_base_url) {
    try {
      const url = new URL(value.decision_base_url)
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error()
    } catch { return t('服务地址须为 HTTP 或 HTTPS 地址，不能包含凭证。') }
  }
  if (!Number.isInteger(value.risk_threshold) || value.risk_threshold < 0 || value.risk_threshold > 100)
    return t('风险阈值须为 0–100 的整数。')
  if (value.trusted_model_ids.length > 32 || value.trusted_model_ids.some(id => id.length > 100))
    return t('可信模型最多 32 个，每个 ID 不超过 100 个字符。')
  if (value.state.length > 4000 || value.decision_model.length > 200 || value.decision_base_url.length > 2048)
    return t('安全设置内容超过长度限制。')
  if (apiKey.value.length > 8192 || apiKey.value.includes('\0')) return t('密钥格式不正确或超过长度限制。')
  if (confirmed.value?.credential_configured && value.decision_base_url !== confirmed.value.decision_base_url.trim() && !apiKey.value.trim())
    return t('更换审查服务地址时，请重新填写密钥或移除原凭证。')
  return ''
}
async function save() {
  if (!draft.value || busy.value || !dirty.value) return
  const value = configuration(draft.value)
  failedAction.value = 'save'
  feedback.value = ''
  error.value = validate(value)
  if (error.value) return
  const request = begin('save')
  try {
    // Send the full configuration: the merged endpoint materializes DTO defaults on PATCH.
    const saved = await api.updateSafetySettings({ ...value, ...(apiKey.value.trim() ? { api_key: apiKey.value.trim() } : {}) }, request.signal)
    if (!request.signal.aborted) { apply(saved); apiKey.value = ''; feedback.value = t('已保存') }
  } catch (reason) {
    if (!request.signal.aborted) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally { if (!request.signal.aborted) action.value = '' }
}
async function removeCredential() {
  if (busy.value || !confirmed.value?.credential_configured || !await confirmAction(t('移除审查服务密钥？需要凭证的审查服务将无法连接。'))) return
  if (busy.value || !confirmed.value?.credential_configured) return
  const request = begin('credential')
  try {
    await api.deleteCredential('safety.decision_model', request.signal)
    if (!request.signal.aborted && draft.value && confirmed.value) {
      draft.value.credential_configured = false
      confirmed.value.credential_configured = false
      apiKey.value = ''
      feedback.value = t('凭证已移除')
    }
  } catch (reason) {
    if (!request.signal.aborted) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally { if (!request.signal.aborted) action.value = '' }
}
function retry() {
  if (failedAction.value === 'load') void load()
  else if (failedAction.value === 'credential') void removeCredential()
  else void save()
}
watch([draft, apiKey], () => { feedback.value = '' }, { deep: true, flush: 'sync' })
async function canLeave() {
  if (action.value === 'save' || action.value === 'credential') return false
  return !dirty.value || await confirmAction(t('安全设置尚未保存，离开会丢失修改。继续？'), false)
}
onBeforeRouteLeave(canLeave)
onBeforeRouteUpdate((to, from) => (to.path === from.path && to.query.tab === 'safety') || canLeave())
onMounted(() => { void load() })
onBeforeUnmount(() => { controller?.abort(); apiKey.value = ''; draft.value = null })
</script>

<template>
  <section class="settings-panel safety-settings" :aria-busy="busy">
    <header class="panel-heading">
      <h2>{{ $t('安全') }}</h2>
      <button type="button" class="text-button" :disabled="busy" @click="load">{{ $t('重新读取') }}</button>
    </header>
    <InkLoader v-if="action === 'load'" />
    <p v-if="unavailable" class="panel-copy" role="status">{{ $t('当前服务不支持安全设置，请更新服务后重试。') }}</p>
    <form v-if="draft && !unavailable" @submit.prevent="save">
      <fieldset :disabled="busy">
        <section class="setting-group" data-settings-field="safety-enabled">
          <label class="safety-switch">
            <input v-model="draft.enabled" type="checkbox">
            <span>{{ $t('提示词安全审查') }}</span>
          </label>
        </section>
        <SafetyModelFields v-model="draft" v-model:api-key="apiKey" :disabled="busy" @remove-credential="removeCredential" />
        <SafetyReviewFields v-model="draft" :models="settings.modelProfiles" :disabled="busy" />
      </fieldset>
      <footer class="safety-actions">
        <button type="submit" class="primary-button" :disabled="busy || !dirty" :aria-busy="action === 'save' || undefined">
          {{ action === 'save' ? $t('正在保存…') : $t('保存安全设置') }}
        </button>
        <span v-if="feedback" class="field-saved" role="status">{{ feedback }}</span>
        <span v-else-if="dirty" class="panel-copy">{{ $t('有未保存的修改') }}</span>
      </footer>
    </form>
    <p v-if="error" class="field-error" role="alert">
      {{ error }}
      <button type="button" class="text-button" :disabled="busy" @click="retry">{{ $t('重试') }}</button>
      <button v-if="failedAction !== 'load'" type="button" class="text-button" :disabled="busy" @click="load">{{ $t('重新读取') }}</button>
    </p>
  </section>
</template>

<style scoped lang="scss">
.safety-settings fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
.safety-switch, .safety-actions { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.safety-switch { font-size: var(--text-md); font-weight: var(--weight-semibold); }
.safety-switch input { accent-color: var(--accent); width: var(--size-18); height: var(--size-18); }
.safety-actions { padding-top: var(--space-5); }
.safety-actions .panel-copy, .safety-actions .field-saved { margin: 0; }
</style>
