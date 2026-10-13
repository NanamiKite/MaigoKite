import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type ChatModelProfile } from '@/api/client'
import ModelSettingsPanel from '@/components/settings/ModelSettingsPanel.vue'
import { createProviderDraft, modelSettingsFingerprint } from '@/config/model-providers'
import { useSettingsStore } from '@/stores/settings'

const cloudProfile: ChatModelProfile = {
  id: 'model_cloud',
  name: 'Cloud',
  provider: 'openai',
  model: 'cloud-model',
  baseUrl: '',
  temperature: 0.4,
  maxTokens: 2048,
  timeoutSeconds: 45,
  credentialConfigured: true,
  isDefault: true,
}

describe('model settings', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('keeps several profiles and starts a new provider with an isolated endpoint', async () => {
    const store = useSettingsStore()
    store.modelProfiles = [cloudProfile]
    store.defaultModelId = cloudProfile.id
    const wrapper = mount(ModelSettingsPanel)

    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.text()).toContain('Cloud')

    await wrapper.get('[data-testid="add-model"]').trigger('click')
    expect(wrapper.get('[data-testid="provider-picker"]').text()).toContain('DeepSeek')
    await wrapper.get('[data-provider-choice="custom"]').trigger('click')

    expect((wrapper.get('[data-testid="model-id"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('[data-testid="base-url"]').element as HTMLInputElement).value).toBe(
      '',
    )
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(store.modelProfiles).toHaveLength(1)
  })

  it('clears an obsolete connection result when the draft changes', async () => {
    const store = useSettingsStore()
    store.validation = { ok: true, message: '连接成功' }
    const wrapper = mount(ModelSettingsPanel)

    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="model-id"]').setValue('another-model')

    expect(store.validation).toBeNull()
  })

  it('shows credential state without exposing storage implementation details', async () => {
    const store = useSettingsStore()
    store.modelProfiles = [cloudProfile]
    store.defaultModelId = cloudProfile.id
    const wrapper = mount(ModelSettingsPanel)

    await wrapper.get('button[aria-label="管理 Cloud 的模型"]').trigger('click')

    expect(wrapper.text()).toContain('密钥已保存')
    expect((wrapper.get('[data-testid="api-key"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.get('[data-testid="api-key"]').attributes('type')).toBe('password')
    expect(wrapper.text()).not.toContain('系统凭证库')
    expect(wrapper.text()).not.toContain('操作系统密钥链')
  })

  it('requires a new key before moving a credentialed profile to another endpoint', async () => {
    const save = vi.spyOn(api, 'saveChatModel')
    const store = useSettingsStore()
    store.modelProfiles = [
      {
        id: 'model_compatible',
        name: 'Compatible',
        provider: 'openai_compatible',
        model: 'old-model',
        baseUrl: 'https://old-gateway.example/v1',
        temperature: 0.7,
        maxTokens: 4096,
        timeoutSeconds: 60,
        credentialConfigured: true,
        isDefault: true,
      },
    ]
    store.defaultModelId = 'model_compatible'
    const wrapper = mount(ModelSettingsPanel)

    await wrapper.get('button[aria-label="管理 Compatible 的模型"]').trigger('click')
    await wrapper.get('[data-testid="base-url"]').setValue('https://api.deepseek.com')
    await wrapper.get('[data-testid="model-id"]').setValue('new-model')

    expect(wrapper.text()).toContain('服务地址或接口已更换，请重新填写 API Key。')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(save).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="api-key"]').attributes('placeholder')).toBe('粘贴 API Key')
    await wrapper.get('[data-testid="api-key"]').setValue('replacement-key')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('creates a named profile and keeps the save confirmation', async () => {
    const store = useSettingsStore()
    let saved: ChatModelProfile | undefined
    vi.spyOn(api, 'saveChatModel').mockImplementation(async (profile) => {
      saved = { ...profile, isDefault: true }
      return saved
    })
    vi.spyOn(api, 'listChatModels').mockImplementation(async () => ({ models: saved ? [saved] : [], defaultModelId: saved?.id || '' }))
    const wrapper = mount(ModelSettingsPanel)

    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="profile-name"]').setValue('Local Qwen')
    await wrapper.get('[data-testid="model-id"]').setValue('local-model')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(store.success).toContain('Local Qwen')
    expect(store.modelProfiles).toHaveLength(1)
    expect(api.saveChatModel).toHaveBeenCalledWith(expect.objectContaining({ name: 'Local Qwen', model: 'local-model', provider: 'ollama', baseUrl: 'http://127.0.0.1:11434' }), '', '')
    expect(api.listChatModels).toHaveBeenCalledTimes(1)
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('creates isolated defaults and normalizes a trailing slash in dirty checks', () => {
    const local = createProviderDraft('ollama', cloudProfile)
    expect(local.model).toBe('')
    expect(local.baseUrl).toBe('http://127.0.0.1:11434')
    expect(local.credentialConfigured).toBe(false)
    expect(modelSettingsFingerprint({ ...local, baseUrl: 'http://127.0.0.1:11434/' })).toBe(
      modelSettingsFingerprint(local),
    )
  })

  it('offers a server-provided catalog without preventing a custom model id', async () => {
    vi.spyOn(api, 'discoverChatModels').mockResolvedValue([{ id: 'server-model', name: 'My served model' }])
    const wrapper = mount(ModelSettingsPanel)
    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="discover-models"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('My served model')
    await wrapper.get('button[aria-label="收起模型列表"]').trigger('click')
    await wrapper.get('[data-testid="model-id"]').setValue('my-private-model')
    expect((wrapper.get('[data-testid="model-id"]').element as HTMLInputElement).value).toBe(
      'my-private-model',
    )
    await wrapper.get('[data-testid="model-id"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.get('[aria-label="已选模型"]').text()).toContain('my-private-model')
  })

  it('discovers models from the configured Base URL without exposing generation tuning', async () => {
    vi.spyOn(api, 'discoverChatModels').mockResolvedValue([
      { id: 'qwen3:8b', name: 'Qwen 3', context_window: 32768, details: 'qwen3 · 8B' },
    ])
    const wrapper = mount(ModelSettingsPanel)

    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="discover-models"]').trigger('click')
    await flushPromises()

    expect(api.discoverChatModels).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: 'ollama',
        baseUrl: 'http://127.0.0.1:11434',
      }),
      '',
      expect.any(AbortSignal),
    )
    expect((wrapper.get('[data-testid="model-id"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.get('[data-testid="model-catalog"]').isVisible()).toBe(true)
    expect(wrapper.text()).toContain('已从 Ollama 获取 1 个模型。')
    expect(wrapper.text()).toContain('32,768 Token 上下文')
    await wrapper.get('[data-model-id="qwen3:8b"]').trigger('click')
    expect(wrapper.get('[data-model-id="qwen3:8b"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('[aria-label="已选模型"]').text()).toContain('qwen3:8b')
    expect(useSettingsStore().modelProfiles).toEqual([])
    expect(wrapper.text()).not.toContain('高级生成参数')
  })

  it('searches and multi-selects in a draft without persisting until save', async () => {
    const save = vi.spyOn(api, 'saveChatModel')
    vi.spyOn(api, 'discoverChatModels').mockResolvedValue([
      { id: 'alpha-chat', name: 'Alpha Chat' },
      { id: 'beta-code', name: 'Beta Code' },
    ])
    const wrapper = mount(ModelSettingsPanel)

    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="discover-models"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="catalog-search"]').setValue('beta')

    expect(wrapper.find('[data-model-id="alpha-chat"]').exists()).toBe(false)
    expect(wrapper.find('[data-model-id="beta-code"]').exists()).toBe(true)
    expect((wrapper.get('[data-testid="model-id"]').element as HTMLInputElement).value).toBe('')

    await wrapper.get('[data-model-id="beta-code"]').trigger('click')
    expect(wrapper.get('[data-model-id="beta-code"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('[aria-label="已选模型"]').text()).toContain('beta-code')
    expect(save).not.toHaveBeenCalled()
    expect(useSettingsStore().modelProfiles).toEqual([])
    await wrapper.get('[data-testid="catalog-search"]').setValue('')
    await wrapper.get('[data-model-id="alpha-chat"]').trigger('click')
    expect(wrapper.findAll('.selected-model')).toHaveLength(2)
    await wrapper.get('[data-model-id="beta-code"]').trigger('click')
    expect(wrapper.get('[aria-label="已选模型"]').text()).not.toContain('beta-code')
    expect(wrapper.get('[aria-label="已选模型"]').text()).toContain('alpha-chat')
    expect(save).not.toHaveBeenCalled()
  })

  it('invalidates a successful connection test when the manual model ID changes', async () => {
    const wrapper = mount(ModelSettingsPanel)
    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="model-id"]').setValue('tested-model')
    const store = useSettingsStore()
    store.validation = { ok: true, message: 'tested-model connected' }
    await wrapper.get('[data-testid="model-id"]').setValue('untested-model')
    expect(store.validation).toBeNull()
    expect(wrapper.text()).not.toContain('tested-model connected')
  })

  it('discards a late discovery response after changing the provider endpoint', async () => {
    let resolve!: (models: { id: string }[]) => void
    const discover = vi.spyOn(api, 'discoverChatModels').mockImplementation(() => new Promise(done => { resolve = done }))
    const wrapper = mount(ModelSettingsPanel)
    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="discover-models"]').trigger('click')
    const signal = discover.mock.calls[0][2]!
    await wrapper.get('[data-testid="base-url"]').setValue('http://localhost:11435')
    expect(signal.aborted).toBe(true)
    resolve([{ id: 'old-endpoint-model' }])
    await flushPromises()
    expect(wrapper.text()).not.toContain('old-endpoint-model')
    expect(wrapper.find('[data-testid="model-catalog"]').exists()).toBe(false)
  })

  it('does not restore stale connection feedback when an in-flight test completes', async () => {
    let resolve!: (result: { ok: boolean; message: string; model: string }) => void
    vi.spyOn(api, 'validateChatModel').mockImplementation(() => new Promise(done => { resolve = done }))
    const wrapper = mount(ModelSettingsPanel)
    await wrapper.get('[data-testid="add-model"]').trigger('click')
    await wrapper.get('[data-provider-choice="ollama"]').trigger('click')
    await wrapper.get('[data-testid="model-id"]').setValue('old-model')
    await wrapper.findAll('button').find(button => button.text() === '测试连接')!.trigger('click')
    expect(api.validateChatModel).toHaveBeenCalledTimes(1)
    await wrapper.get('[data-testid="model-id"]').setValue('new-model')
    resolve({ ok: true, message: 'OK', model: 'old-model' })
    await flushPromises()
    expect(useSettingsStore().validation).toBeNull()
    expect(useSettingsStore().validating).toBe(false)
    expect(wrapper.text()).not.toContain('连接成功：old-model')
  })
})
