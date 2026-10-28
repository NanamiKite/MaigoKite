import { t } from '@/i18n'
import { confirmAction } from '@/utils/confirm-action'
import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import {
  COMPATIBLE_PROVIDER_PRESETS,
  getModelProvider,
  modelSettingsFingerprint,
} from '@/config/model-providers'
import {
  findModelPreset as findPreset,
  groupModelProfiles,
  modelServiceName,
  normalizeEndpoint,
} from '@/config/model-groups'
import { useSettingsStore } from '@/stores/settings'
import {
  api,
  type ChatModelProfile,
  type ChatModelProvider,
  type DiscoveredChatModel,
} from '@/api/client'

import { type ProviderChoice } from '@/config/model-provider-choices'

type PanelMode = 'list' | 'providers' | 'editor'

function newEditId(): string {
  // UUID accepts 32 hex digits; getRandomValues also works on LAN HTTP pages.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
    value.toString(16).padStart(2, '0'),
  ).join('')
}

// Keep edits in a draft so endpoint changes cannot reuse a saved service's key.
export function useModelSettings() {
  const settings = useSettingsStore()
  const mode = ref<PanelMode>('list')
  const selectedId = ref('')
  const apiKey = ref('')
  const revealKey = ref(false)
  const discoveredModels = ref<DiscoveredChatModel[]>([])
  const discovering = ref(false)
  const discoveryMessage = ref('')
  const discoveryFailed = ref(false)
  const catalogOpen = ref(false)
  const customService = ref(false)
  const selectedModels = ref<string[]>([])
  const manualModelId = ref('')
  const capacityOverrides = ref<Record<string, string>>({})
  let operationId = newEditId()
  let expectedRevision = settings.modelRevision
  let discoveryController: AbortController | null = null

  function blankProfile(): ChatModelProfile {
    return {
      id: '',
      name: '',
      provider: 'ollama',
      model: '',
      baseUrl: 'http://127.0.0.1:11434',
      temperature: 0.7,
      maxTokens: 4096,
      contextWindow: undefined,
      timeoutSeconds: 60,
      credentialConfigured: false,
      isDefault: false,
    }
  }

  const draft = reactive<ChatModelProfile>(blankProfile())
  const original = ref<ChatModelProfile | null>(null)
  const originalModels = ref<ChatModelProfile[]>([])
  const modelGroups = computed(() => groupModelProfiles(settings.modelProfiles))
  const provider = computed(() => getModelProvider(draft.provider))
  const compatiblePreset = computed(() => findPreset(draft.baseUrl, draft.provider))
  const displayProviderName = computed(() =>
    customService.value
      ? t('中转站 / 自定义服务')
      : t(compatiblePreset.value?.name ?? provider.value.name),
  )
  const requiresEndpoint = computed(
    () => customService.value || draft.provider === 'openai_compatible',
  )
  const endpointHelp = computed(() =>
    customService.value
      ? draft.provider === 'anthropic'
        ? t('填写服务地址，不含 /v1/messages。')
        : t('填写服务提供的 Base URL（通常含 /v1），不含 /chat/completions。')
      : compatiblePreset.value
        ? t('请使用与你的地域或套餐对应的服务地址。')
        : t(provider.value.baseUrlHelp),
  )
  const modelOptions = computed(() => discoveredModels.value)
  const modelIds = computed(() => [
    ...new Set([
      ...selectedModels.value,
      ...(manualModelId.value.trim() ? [manualModelId.value.trim()] : []),
    ]),
  ])
  const modelCount = computed(() => modelIds.value.length)
  const selectedModelInfo = computed(() => {
    const item = discoveredModels.value.find((model) => model.id === draft.model)
    if (!item) return ''
    return [
      item.owned_by,
      item.context_window
        ? t('{0} Token 上下文', [item.context_window.toLocaleString('en-US')])
        : '',
      item.details,
    ]
      .filter(Boolean)
      .join(' · ')
  })
  const endpointChanged = computed(() =>
    Boolean(
      original.value &&
      original.value.credentialConfigured &&
      (original.value.provider !== draft.provider ||
        normalizeEndpoint(original.value.baseUrl) !== normalizeEndpoint(draft.baseUrl)),
    ),
  )
  const keyRequired = computed(
    () => (compatiblePreset.value?.apiKeyMode ?? provider.value.apiKeyMode) === 'required',
  )
  const canDiscover = computed(() =>
    Boolean(
      (!requiresEndpoint.value || draft.baseUrl.trim()) &&
      (!keyRequired.value ||
        apiKey.value.trim() ||
        (draft.credentialConfigured && !endpointChanged.value)),
    ),
  )
  const canSave = computed(() =>
    Boolean(
      draft.name.trim() &&
      !Object.values(capacityErrors.value).some(Boolean) &&
      modelCount.value &&
      manualModelId.value.trim().length <= 200 &&
      (!requiresEndpoint.value || draft.baseUrl.trim()) &&
      (!keyRequired.value ||
        apiKey.value.trim() ||
        (draft.credentialConfigured && !endpointChanged.value)),
    ),
  )
  function catalogCapacity(model: string): number | undefined {
    const capacity = discoveredModels.value.find((item) => item.id === model)?.context_window
    return capacity !== undefined && Number.isSafeInteger(capacity) && capacity > 0
      ? capacity
      : undefined
  }
  const capacityErrors = computed(() =>
    Object.fromEntries(
      modelIds.value.map((model) => {
        const value = Object.prototype.hasOwnProperty.call(capacityOverrides.value, model)
          ? capacityOverrides.value[model].trim()
          : ''
        const valid =
          !value ||
          (/^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0)
        return [model, valid ? '' : t('容量须为正整数，或留空自动获取。')]
      }),
    ),
  )
  function modelCapacity(model: string): number | undefined {
    if (Object.prototype.hasOwnProperty.call(capacityOverrides.value, model)) {
      const value = capacityOverrides.value[model].trim()
      return value ? Number(value) : catalogCapacity(model)
    }
    // A directory omitting capacity must not erase a previously saved value.
    const existing = originalModels.value.find(
      (item) =>
        item.model === model &&
        item.provider === draft.provider &&
        normalizeEndpoint(item.baseUrl) === normalizeEndpoint(draft.baseUrl),
    )
    return catalogCapacity(model) ?? existing?.contextWindow
  }
  function setModelCapacity(model: string, value: string) {
    capacityOverrides.value = { ...capacityOverrides.value, [model]: value }
    settings.invalidateModelValidation()
    settings.error = null
  }
  function modelCapacityInput(model: string): string {
    return Object.prototype.hasOwnProperty.call(capacityOverrides.value, model)
      ? capacityOverrides.value[model]
      : String(modelCapacity(model) ?? '')
  }
  const isDirty = computed(
    () =>
      !original.value ||
      modelSettingsFingerprint(draft) !== modelSettingsFingerprint(original.value) ||
      draft.name !== original.value.name ||
      selectedModels.value.length !==
        new Set(originalModels.value.map((item) => item.model)).size ||
      originalModels.value.some((item) => !selectedModels.value.includes(item.model)) ||
      originalModels.value.some((item) => modelCapacity(item.model) !== item.contextWindow) ||
      Boolean(manualModelId.value.trim()) ||
      Boolean(apiKey.value.trim()),
  )

  watch(
    () => [
      modelSettingsFingerprint(draft),
      draft.name,
      apiKey.value,
      manualModelId.value,
      ...selectedModels.value,
    ],
    () => {
      settings.invalidateModelValidation()
      settings.error = null
    },
    { flush: 'sync' },
  )

  watch(
    () => [draft.id, draft.provider, draft.baseUrl, apiKey.value],
    () => {
      cancelDiscovery()
      catalogOpen.value = false
      discoveredModels.value = []
      discoveryMessage.value = ''
      discoveryFailed.value = false
    },
    { flush: 'sync' },
  )
  onScopeDispose(() => {
    cancelDiscovery()
    settings.invalidateModelValidation()
  })

  watch(() => [draft.provider, draft.baseUrl], clearModelSelection, { flush: 'sync' })

  watch(
    () => settings.modelProfiles.map((profile) => profile.id),
    (ids) => {
      if (!settings.saving && selectedId.value && !ids.includes(selectedId.value)) closeEditor()
    },
  )

  function isCustomService(profile: ChatModelProfile): boolean {
    return (
      profile.provider !== 'ollama' &&
      !findPreset(profile.baseUrl, profile.provider) &&
      Boolean(profile.baseUrl.trim())
    )
  }

  function profileProviderName(profile: ChatModelProfile): string {
    if (isCustomService(profile)) {
      return profile.provider === 'anthropic'
        ? t('Anthropic 中转 / 自定义')
        : t('OpenAI 中转 / 自定义')
    }
    return t(
      findPreset(profile.baseUrl, profile.provider)?.name ??
        getModelProvider(profile.provider).name,
    )
  }

  function profileCredential(profile: ChatModelProfile): { label: string; tone: string } {
    const definition = getModelProvider(profile.provider)
    const keyMode =
      findPreset(profile.baseUrl, profile.provider)?.apiKeyMode ?? definition.apiKeyMode
    if (keyMode === 'none') return { label: t('无需密钥'), tone: 'neutral' }
    if (profile.credentialConfigured) return { label: t('密钥已配置'), tone: 'ready' }
    if (keyMode === 'required') {
      return { label: t('需要密钥'), tone: 'missing' }
    }
    return { label: t('密钥可选'), tone: 'neutral' }
  }

  function resetEditor() {
    operationId = newEditId()
    expectedRevision = settings.modelRevision
    cancelDiscovery()
    selectedId.value = ''
    clearModelSelection()
    customService.value = false
    original.value = null
    originalModels.value = []
    Object.assign(draft, blankProfile())
    apiKey.value = ''
    revealKey.value = false
    discoveredModels.value = []
    discoveryMessage.value = ''
    discoveryFailed.value = false
    catalogOpen.value = false
    settings.clearModelFeedback()
  }

  function showProviderPicker() {
    resetEditor()
    mode.value = 'providers'
  }

  function beginAdd(choice: ProviderChoice) {
    resetEditor()
    selectProvider(choice.provider)
    draft.name = choice.name
    if (choice.presetId) applyPreset(choice.presetId)
    if (choice.custom) {
      customService.value = true
      draft.baseUrl = ''
    }
    mode.value = 'editor'
  }

  function selectProfile(id: string) {
    const item = settings.modelProfiles.find((profile) => profile.id === id)
    if (!item) return
    operationId = newEditId()
    expectedRevision = settings.modelRevision
    const group = modelGroups.value.find((group) =>
      group.models.some((profile) => profile.id === id),
    )
    const members = group?.models || [item]
    // Prefer a configured credential for discovery, without changing any saved credential.
    const seed =
      members.find((profile) => profile.id === id && profile.credentialConfigured) ||
      members.find((profile) => profile.credentialConfigured) ||
      item
    const name = modelServiceName(item)
    selectedId.value = item.id
    original.value = { ...seed, name }
    originalModels.value = members.map((profile) => ({ ...profile }))
    Object.assign(draft, seed, { name })
    selectedModels.value = [...new Set(members.map((profile) => profile.model))]
    draft.model = seed.model
    manualModelId.value = ''
    customService.value = isCustomService(item)
    apiKey.value = ''
    revealKey.value = false
    discoveredModels.value = []
    discoveryMessage.value = ''
    discoveryFailed.value = false
    catalogOpen.value = false
    settings.clearModelFeedback()
    mode.value = 'editor'
  }

  function closeEditor() {
    resetEditor()
    mode.value = 'list'
  }

  function selectProvider(value: ChatModelProvider) {
    clearModelSelection()
    customService.value = false
    draft.provider = value
    draft.baseUrl = getModelProvider(value).defaultBaseUrl
    draft.model = ''
    draft.credentialConfigured = false
    apiKey.value = ''
  }

  function applyPreset(id: string) {
    const preset = COMPATIBLE_PROVIDER_PRESETS.find((item) => item.id === id)
    if (!preset) return
    clearModelSelection()
    customService.value = false
    draft.provider = preset.provider ?? 'openai_compatible'
    draft.baseUrl = preset.baseUrl
    draft.model = ''
    draft.credentialConfigured = false
    apiKey.value = ''
  }

  function handleProviderChange(event: Event) {
    selectProvider((event.target as HTMLSelectElement).value as ChatModelProvider)
  }

  function handlePresetChange(event: Event) {
    const presetId = (event.target as HTMLSelectElement).value
    if (presetId) applyPreset(presetId)
    else customService.value = true
  }

  function handleProtocolChange(event: Event) {
    const value = (event.target as HTMLSelectElement).value
    if (value !== 'openai_compatible' && value !== 'anthropic') return
    clearModelSelection()
    // Keep the gateway URL; endpointChanged prevents reuse of a different protocol's stored key.
    draft.provider = value
    draft.model = ''
  }

  async function save() {
    if (!canSave.value || settings.saving) return
    if (manualModelId.value.trim()) addModel(manualModelId.value)
    manualModelId.value = ''
    const snapshot = { ...draft }
    const removed = originalModels.value.filter(
      (item) =>
        !selectedModels.value.includes(item.model) &&
        settings.modelProfiles.some((profile) => profile.id === item.id),
    )
    if (
      removed.length &&
      !(await confirmAction(
        t('保存后将移除 {0} 个模型：{1}。继续？', [
          removed.length,
          removed.map((item) => item.model).join('、'),
        ]),
      ))
    )
      return
    if (!canSave.value || settings.saving || draft.id !== snapshot.id) return
    const targets = selectedModels.value.flatMap((model): ChatModelProfile[] => {
      const existing = originalModels.value.filter((item) => item.model === model)
      const capacity = modelCapacity(model)
      // Keep IDs, defaults and per-model generation settings; order is not identity.
      if (existing.length)
        return existing.map((item) => ({
          ...item,
          name: snapshot.name,
          provider: snapshot.provider,
          baseUrl: snapshot.baseUrl,
          contextWindow: capacity,
        }))
      return [
        {
          ...snapshot,
          id: '',
          model,
          contextWindow: capacity,
          name: snapshot.name,
          credentialConfigured: false,
          isDefault: false,
        },
      ]
    })
    const result = await settings.applyModelEdit({
      operationId,
      expectedRevision,
      profiles: targets,
      apiKey: apiKey.value.trim(),
      reuseCredentials: snapshot.credentialConfigured && !endpointChanged.value,
      removedIds: removed.map((item) => item.id),
    })
    if (result.revision) expectedRevision = result.revision
    if (!result.ok) return
    const message = settings.success
    closeEditor()
    settings.success = message
  }

  function openCatalog() {
    catalogOpen.value = true
    if (!discoveredModels.value.length) void discoverModels()
  }

  function cancelDiscovery() {
    discoveryController?.abort()
    discoveryController = null
    discovering.value = false
  }

  function closeCatalog() {
    catalogOpen.value = false
    cancelDiscovery()
  }

  function clearModelSelection() {
    selectedModels.value = []
    manualModelId.value = ''
    capacityOverrides.value = {}
    draft.model = ''
  }

  function addModel(value: string): boolean {
    const model = value.trim()
    if (!model || model.length > 200) return false
    if (!selectedModels.value.includes(model))
      selectedModels.value = [...selectedModels.value, model]
    if (!draft.model) draft.model = model
    return true
  }

  function removeModel(modelId: string) {
    selectedModels.value = selectedModels.value.filter((model) => model !== modelId)
    if (draft.model === modelId) draft.model = selectedModels.value[0] || ''
  }

  function useCatalogModel(modelId: string) {
    if (selectedModels.value.includes(modelId)) removeModel(modelId)
    else addModel(modelId)
  }

  async function discoverModels() {
    if (!canDiscover.value || discovering.value) return
    const controller = new AbortController()
    discoveryController = controller
    discovering.value = true
    discoveredModels.value = []
    discoveryMessage.value = ''
    discoveryFailed.value = false
    catalogOpen.value = true
    settings.clearModelFeedback()
    try {
      // Bind the response to this form snapshot; changing endpoint/key aborts it.
      const target = { id: draft.id, provider: draft.provider, baseUrl: draft.baseUrl }
      const models = await api.discoverChatModels(target, apiKey.value.trim(), controller.signal)
      if (discoveryController !== controller) return
      discoveredModels.value = models
      discoveryMessage.value = models.length
        ? t('已从 {0} 获取 {1} 个模型。', [displayProviderName.value, models.length])
        : t('未获取到可用模型，可手动填写模型 ID。')
    } catch (reason) {
      if (discoveryController !== controller) return
      discoveryFailed.value = true
      discoveryMessage.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      if (discoveryController === controller) {
        discoveryController = null
        discovering.value = false
      }
    }
  }

  async function makeDefault(id = draft.id) {
    if (!id) return
    const item = settings.modelProfiles.find((profile) => profile.id === id)
    if (!item || item.isDefault) return
    if (await settings.setDefaultModel(id)) {
      expectedRevision = settings.modelRevision
      originalModels.value.forEach((item) => {
        item.isDefault = item.id === id
      })
      if (draft.id === id) {
        draft.isDefault = true
        if (original.value) original.value.isDefault = true
      }
    }
  }

  async function remove() {
    const selected = draft.id
    if (
      !draft.id ||
      !(await confirmAction(
        t('删除服务“{0}”及其 {1} 个模型？', [draft.name, originalModels.value.length]),
      ))
    )
      return
    if (settings.saving || draft.id !== selected) return
    const ids = originalModels.value.map((item) => item.id)
    const result = await settings.applyModelEdit({
      operationId,
      expectedRevision,
      profiles: [],
      apiKey: '',
      reuseCredentials: false,
      removedIds: ids,
    })
    if (result.revision) expectedRevision = result.revision
    if (result.ok) {
      const message = settings.success
      closeEditor()
      settings.success = message
    }
  }
  return {
    settings,
    mode,
    selectedId,
    modelGroups,
    apiKey,
    revealKey,
    discoveredModels,
    discovering,
    discoveryMessage,
    discoveryFailed,
    catalogOpen,
    customService,
    draft,
    provider,
    compatiblePreset,
    displayProviderName,
    modelOptions,
    selectedModels,
    modelIds,
    capacityErrors,
    modelCapacity,
    modelCapacityInput,
    setModelCapacity,
    manualModelId,
    modelCount,
    addModel,
    removeModel,
    selectedModelInfo,
    endpointChanged,
    endpointHelp,
    requiresEndpoint,
    keyRequired,
    canDiscover,
    canSave,
    isDirty,
    findPreset,
    profileProviderName,
    profileCredential,
    showProviderPicker,
    beginAdd,
    selectProfile,
    closeEditor,
    handleProviderChange,
    handlePresetChange,
    handleProtocolChange,
    save,
    openCatalog,
    closeCatalog,
    useCatalogModel,
    discoverModels,
    makeDefault,
    remove,
  }
}
