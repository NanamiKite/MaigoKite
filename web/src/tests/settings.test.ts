import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type ChatModelProfile, type ChatModelSettings } from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import Settings from '@/views/Settings.vue'
import { testRouter } from './ui-harness'

const localSettings: ChatModelSettings = {
  provider: 'openai_compatible',
  model: 'local-model',
  baseUrl: 'http://127.0.0.1:1234/v1',
  temperature: 0.7,
  maxTokens: 4096,
  timeoutSeconds: 60,
  credentialConfigured: false,
}

describe('settings store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('loads and saves the per-user assistant name', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({ assistantName: '小七' })
    const update = vi
      .spyOn(api, 'updateUserPreferences')
      .mockResolvedValue({ assistantName: '小七 Agent' })
    const store = useSettingsStore()

    await store.loadPreferences()
    expect(store.profile.assistantName).toBe('小七')

    const saved = await store.savePreferences('小七 Agent')
    expect(saved).toBe(true)
    expect(update).toHaveBeenCalledWith({ assistantName: '小七 Agent' })
    expect(store.profile.assistantName).toBe('小七 Agent')
    expect(localStorage.getItem('maigokite_user_preferences')).toContain('小七 Agent')
  })

  it('loads persisted model settings from the application service', async () => {
    const profile: ChatModelProfile = {
      ...localSettings,
      id: 'model_local',
      name: 'Local',
      isDefault: true,
    }
    vi.spyOn(api, 'listChatModels').mockResolvedValue({
      models: [profile],
      defaultModelId: profile.id,
      revision: 'a'.repeat(64),
    })
    vi.spyOn(api, 'listCredentialReferences').mockResolvedValue([])
    const store = useSettingsStore()

    await store.loadSettings()

    expect(store.modelProfiles).toEqual([profile])
    expect(store.defaultModelId).toBe(profile.id)
    expect(store.loaded).toBe(true)
  })

  it('saves credentials in a model-profile-scoped request', async () => {
    const cloudSettings: ChatModelProfile = {
      ...localSettings,
      id: 'model_cloud',
      name: 'Cloud',
      provider: 'openai',
      model: 'gpt-test',
      baseUrl: '',
      isDefault: true,
    }
    const save = vi.spyOn(api, 'updateChatModels').mockResolvedValue({
      models: [{ ...cloudSettings, credentialConfigured: true }],
      defaultModelId: cloudSettings.id,
      revision: 'b'.repeat(64),
      savedIds: [cloudSettings.id],
      removedIds: [],
      error: null,
    })
    const store = useSettingsStore()

    const edit = {
      operationId: crypto.randomUUID(),
      expectedRevision: 'a'.repeat(64),
      profiles: [cloudSettings],
      apiKey: 'secret-key',
      reuseCredentials: false,
      removedIds: [],
    }
    const saved = await store.applyModelEdit(edit)

    expect(saved.ok).toBe(true)
    expect(store.modelProfiles[0].id).toBe('model_cloud')
    expect(save).toHaveBeenCalledWith(edit)
    expect(store.success).toContain('已保存')
  })

  it('presents settings without release phases or the internal project name', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    vi.spyOn(api, 'listChatModels').mockResolvedValue({
      models: [],
      defaultModelId: '',
      revision: 'a'.repeat(64),
    })
    vi.spyOn(api, 'listCredentialReferences').mockResolvedValue([])
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({ assistantName: '小七' })
    vi.spyOn(api, 'getPermissionSettings').mockResolvedValue({ default_mode: 'workspace' })
    const router = await testRouter('/settings')

    // This legacy assembly checks the name form; the independent search widget
    // has its own keyboard/focus coverage in final-presentation.test.ts.
    const wrapper = mount(Settings, {
      global: { plugins: [pinia, router], stubs: { SettingsSearch: true } },
    })
    await flushPromises()

    for (const label of ['外观与称呼', '模型', '工具', '权限', '工作区'])
      expect(wrapper.text()).toContain(label)
    expect(wrapper.get('input').element.value).toBe('小七')
    expect(wrapper.text()).not.toContain('Phase 1')
    expect(wrapper.text()).not.toContain('MaigoKite')
  })
})
