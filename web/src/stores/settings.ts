import { t } from '@/i18n'
/**
 * Settings Store — 管理用户配置（模型、API Key、权限、搜索工具等）
 */

import { defineStore } from 'pinia'
import { onScopeDispose, ref } from 'vue'
import {
  copySubagentSettings,
  emptySubagentSettings,
  validSubagentSettings,
  type SubagentSettings,
} from '@/config/subagents'
import {
  api,
  type ChatModelProfile,
  type ChatModelSettings,
  type CredentialEntry,
  type PermissionSettings,
  type WorkspaceRuntimeStatus,
} from '@/api/client'

const PREFERENCES_CACHE_KEY = import.meta.env.VITE_MOCK === '1' ? 'maigokite_user_preferences_demo' : 'maigokite_user_preferences'
const SUBAGENT_DEFAULTS_KEY = import.meta.env.VITE_MOCK === '1' ? 'maigokite_subagent_defaults_demo' : 'maigokite_subagent_defaults'

function cachedSubagentDefaults(): SubagentSettings {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(SUBAGENT_DEFAULTS_KEY) || 'null')
    if (validSubagentSettings(value)) return copySubagentSettings(value)
  } catch { /* Invalid or unavailable local storage keeps subagents disabled. */ }
  return emptySubagentSettings()
}

function cachedAssistantName(): string {
  try {
    const value = JSON.parse(localStorage.getItem(PREFERENCES_CACHE_KEY) || '{}') as { assistantName?: unknown }
    return typeof value.assistantName === 'string' && value.assistantName.trim() ? value.assistantName.trim() : '助手'
  } catch {
    return '助手'
  }
}

function cacheAssistantName(assistantName: string) {
  try { localStorage.setItem(PREFERENCES_CACHE_KEY, JSON.stringify({ assistantName })) }
  catch { /* The server remains the source of truth when storage is unavailable. */ }
}

export const useSettingsStore = defineStore('settings', () => {
  const credentials = ref<Record<string, CredentialEntry>>({})
  const modelProfiles = ref<ChatModelProfile[]>([])
  const defaultModelId = ref('')
  const profile = ref({ assistantName: cachedAssistantName() })
  const permissions = ref<PermissionSettings>({ default_mode: 'workspace' })
  const subagentDefaults = ref(cachedSubagentDefaults())
  // Other windows (including the capsule) must use newly saved defaults too.
  function syncSubagentDefaults(event: StorageEvent) {
    if (event.key === SUBAGENT_DEFAULTS_KEY || event.key === null)
      subagentDefaults.value = cachedSubagentDefaults()
  }
  window.addEventListener('storage', syncSubagentDefaults)
  onScopeDispose(() => window.removeEventListener('storage', syncSubagentDefaults))
  const runtimeStatus = ref<WorkspaceRuntimeStatus | null>(null)
  const runtimeLoading = ref(false)
  const runtimeError = ref<string | null>(null)

  const loaded = ref(false)
  const profileLoaded = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)
  const success = ref<string | null>(null)
  const validating = ref(false)
  const validation = ref<{ ok: boolean; message: string } | null>(null)

  let profileRequest: Promise<void> | null = null
  let runtimeRequest = 0
  let validationRequest = 0

  function saveSubagentDefaults(value: SubagentSettings) {
    if (!validSubagentSettings(value))
      throw new Error(t('每次任务限 0–32 个，并行限 1–8 个且不能大于任务派发上限；允许指定的模型最多 32 个。'))
    const next = copySubagentSettings(value)
    try {
      localStorage.setItem(SUBAGENT_DEFAULTS_KEY, JSON.stringify(next))
    } catch {
      throw new Error(t('无法保存设置，请检查本地存储是否可用。'))
    }
    subagentDefaults.value = next
  }

  async function loadPreferences() {
    if (profileLoaded.value) return
    if (profileRequest) return profileRequest
    profileRequest = (async () => {
      try {
        profile.value = await api.getUserPreferences()
        cacheAssistantName(profile.value.assistantName)
        profileLoaded.value = true
      } catch (reason) {
        error.value = reason instanceof Error ? reason.message : String(reason)
      } finally {
        profileRequest = null
      }
    })()
    return profileRequest
  }

  async function savePreferences(assistantName: string) {
    saving.value = true
    error.value = null
    success.value = null
    try {
      profile.value = await api.updateUserPreferences({ assistantName })
      cacheAssistantName(profile.value.assistantName)
      profileLoaded.value = true
      success.value = t("界面称呼已更新。")
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return false
    } finally {
      saving.value = false
    }
  }

  async function loadPermissionSettings() {
    try { permissions.value = await api.getPermissionSettings() }
    catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  }

  async function savePermissionSettings(value: PermissionSettings) {
    saving.value = true
    error.value = null
    success.value = null
    try {
      permissions.value = await api.updatePermissionSettings(value)
      success.value = t("默认权限已保存。")
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return false
    } finally { saving.value = false }
  }

  async function loadRuntimeStatus(folderId = '') {
    const request = ++runtimeRequest
    runtimeLoading.value = true
    runtimeStatus.value = null
    runtimeError.value = null
    try {
      const result = await api.getWorkspaceRuntimeStatus(folderId)
      if (request === runtimeRequest) runtimeStatus.value = result
    } catch (reason) {
      if (request === runtimeRequest)
        runtimeError.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      if (request === runtimeRequest) runtimeLoading.value = false
    }
  }

  /**
   * 从后端加载当前配置
   */
  async function loadSettings() {
    saving.value = true
    error.value = null
    success.value = null
    try {
      const [catalog, statuses] = await Promise.all([
        api.listChatModels(),
        api.listCredentialReferences(),
      ])
      modelProfiles.value = catalog.models
      defaultModelId.value = catalog.defaultModelId
      credentials.value = Object.fromEntries(statuses.map(item => [item.name, { service: item.name, configured: item.configured, validated: item.validated }]))
      loaded.value = true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      saving.value = false
    }
  }

  async function loadModelProfiles() {
    const catalog = await api.listChatModels()
    modelProfiles.value = catalog.models
    defaultModelId.value = catalog.defaultModelId
  }

  function projectModelProfile(saved: ChatModelProfile) {
    if (saved.isDefault) modelProfiles.value.forEach(item => { item.isDefault = false })
    const index = modelProfiles.value.findIndex(item => item.id === saved.id)
    if (index >= 0) modelProfiles.value[index] = saved
    else modelProfiles.value.push(saved)
    if (saved.isDefault || !defaultModelId.value) defaultModelId.value = saved.id
  }

  async function saveModelProfile(profile: ChatModelProfile, apiKey = '') {
    saving.value = true
    error.value = null
    success.value = null
    validation.value = null
    try {
      const saved = await api.saveChatModel(profile, apiKey)
      projectModelProfile(saved)
      success.value = t("模型配置“{0}”已保存。", [saved.name])
      return saved
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return null
    } finally {
      saving.value = false
    }
  }

  async function saveModelProfiles(profiles: ChatModelProfile[], apiKey = '', credentialSourceId = '', removedIds: string[] = []) {
    if (saving.value || !profiles.length) return null
    saving.value = true
    error.value = null
    success.value = null
    validation.value = null
    const saved: ChatModelProfile[] = []
    let key = apiKey
    let sourceId = credentialSourceId
    let removedCount = 0
    try {
      // The existing catalog is read/modify/write: save serially, not in parallel.
      for (const item of profiles) {
        // Grouping is presentation, not proof of shared credentials. Only copy to a
        // new/unconfigured model, or when the user explicitly replaces the service key.
        const existing = modelProfiles.value.find(profile => profile.id === item.id)
        const endpointEdited = existing && (existing.provider !== item.provider || existing.baseUrl !== item.baseUrl)
        const copyFrom = !apiKey && item.credentialConfigured && endpointEdited
          ? item.id
          : item.id !== sourceId && (Boolean(apiKey) || !item.credentialConfigured) ? sourceId : ''
        const result = await api.saveChatModel(item, key, copyFrom)
        projectModelProfile(result)
        saved.push(result)
        if (result.credentialConfigured) {
          if (key || !sourceId) sourceId = result.id
          key = ''
        }
      }
      // Save first: a partial update must not discard the old, still usable models.
      for (const id of removedIds) {
        await api.deleteChatModel(id)
        removedCount++
      }
      await loadModelProfiles()
      success.value = saved.length === 1 ? t("模型配置“{0}”已保存。", [saved[0].name]) : t("已保存 {0} 个模型。", [saved.length])
      return saved
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : String(reason)
      const message = t("已保存 {0} 个模型{1}，更新未完成：{2}。请重试。", [saved.length, removedIds.length ? t("，已移除 {0} 个", [removedCount]) : '', detail])
      // A failed HTTP response may follow a successful server write; reload reality.
      try { await loadModelProfiles() } catch { /* Keep confirmed local results. */ }
      error.value = message
      return null
    } finally {
      saving.value = false
    }
  }

  async function setDefaultModel(modelId: string) {
    saving.value = true
    error.value = null
    try {
      const catalog = await api.setDefaultChatModel(modelId)
      modelProfiles.value = catalog.models
      defaultModelId.value = catalog.defaultModelId
      success.value = t("默认模型已更新。")
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return false
    } finally { saving.value = false }
  }

  async function removeModelProfile(modelId: string) {
    saving.value = true
    error.value = null
    try {
      await api.deleteChatModel(modelId)
      await loadModelProfiles()
      success.value = t("模型配置已删除。")
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return false
    } finally { saving.value = false }
  }

  async function removeModelProfiles(modelIds: string[]) {
    if (saving.value || !modelIds.length) return false
    saving.value = true
    error.value = null
    success.value = null
    let removed = 0
    try {
      for (const id of modelIds) {
        await api.deleteChatModel(id)
        removed++
      }
      await loadModelProfiles()
      success.value = t("模型服务已删除。")
      return true
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : String(reason)
      try { await loadModelProfiles() } catch { /* Keep the error visible for retry. */ }
      error.value = t("已删除 {0} 个模型，删除未完成：{1}", [removed, detail])
      return false
    } finally { saving.value = false }
  }

  async function validateChatModel(cfg: ChatModelSettings, apiKey = '') {
    const request = ++validationRequest
    validating.value = true
    error.value = null
    success.value = null
    validation.value = null
    try {
      const result = await api.validateChatModel(cfg, apiKey)
      if (request !== validationRequest) return false
      validation.value = { ok: result.ok, message: result.ok ? t("连接成功：{0}", [result.model]) : result.message }
      return result.ok
    } catch (reason) {
      if (request !== validationRequest) return false
      const message = reason instanceof Error ? reason.message : String(reason)
      validation.value = { ok: false, message }
      return false
    } finally {
      if (request === validationRequest) validating.value = false
    }
  }

  function invalidateModelValidation() {
    validationRequest++
    validating.value = false
    validation.value = null
  }

  function clearModelFeedback() {
    invalidateModelValidation()
    error.value = null
    success.value = null
  }

  /**
   * 保存凭证（API Key）
   */
  async function saveCredential(service: string, apiKey: string) {
    saving.value = true
    error.value = null
    success.value = null
    try {
      await api.saveCredential(service, apiKey)
      credentials.value[service] = {
        service,
        configured: true,
        validated: false,
        updated_at: new Date().toISOString(),
      }
      success.value = t("联网工具凭证已保存。")
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return false
    } finally {
      saving.value = false
    }
  }

  /**
   * 删除凭证
   */
  async function deleteCredential(service: string) {
    saving.value = true
    error.value = null
    success.value = null
    try {
      await api.deleteCredential(service)
      delete credentials.value[service]
      success.value = t("联网工具凭证已移除。")
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      return false
    } finally {
      saving.value = false
    }
  }

  return {
    modelProfiles,
    defaultModelId,
    credentials,
    profile,
    permissions,
    subagentDefaults,
    runtimeStatus,
    runtimeLoading,
    runtimeError,
    loaded,
    profileLoaded,
    saving,
    error,
    success,
    validating,
    validation,
    loadPreferences,
    savePreferences,
    saveSubagentDefaults,
    loadPermissionSettings,
    savePermissionSettings,
    loadRuntimeStatus,
    loadSettings,
    loadModelProfiles,
    saveModelProfile,
    saveModelProfiles,
    setDefaultModel,
    removeModelProfile,
    removeModelProfiles,
    validateChatModel,
    invalidateModelValidation,
    clearModelFeedback,
    saveCredential,
    deleteCredential,
  }
})
