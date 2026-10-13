import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type ToolSetting } from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import { useUIStore, validAppearance, type Appearance } from '@/stores/ui'
import { searchSettings } from '@/config/settings-fields'
import SecretField from '@/components/common/SecretField.vue'
import InkConfirm from '@/components/common/InkConfirm.vue'
import ModelCatalogPicker from '@/components/settings/ModelCatalogPicker.vue'
import ToolSettingsPanel from '@/components/settings/ToolSettingsPanel.vue'
import SearchToolSettingsPanel from '@/components/settings/SearchToolSettingsPanel.vue'
import AppearanceSettingsPanel from '@/components/settings/AppearanceSettingsPanel.vue'
import FileTree from '@/components/file-explorer/FileTree.vue'
import AuditLog from '@/views/AuditLog.vue'
import Settings from '@/views/Settings.vue'
import { mockDialogs, testRouter } from './ui-harness'
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
beforeEach(() => {
  mockDialogs()
  vi.spyOn(api, 'getUserPreferences').mockResolvedValue({ assistantName: '小七' })
  vi.spyOn(api, 'getPermissionSettings').mockResolvedValue({ default_mode: 'workspace' })
  vi.spyOn(api, 'listChatModels').mockResolvedValue({ models: [], defaultModelId: '' })
  vi.spyOn(api, 'listCredentialReferences').mockResolvedValue([])
})
afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
  else Reflect.deleteProperty(navigator, 'clipboard')
})
const appearance: Appearance = { theme: 'light', accent: 'stone', texture: true, greetingName: '你', avatar: '助', capsuleShortcut: 'Alt+Space' }
describe('final presentation preferences and search', () => {
  it('accepts old appearance data and validates the optional font preference', () => {
    expect(validAppearance(appearance)).toBe(true)
    for (const textSize of ['small','default','large']) expect(validAppearance({ ...appearance, textSize })).toBe(true)
    expect(validAppearance({ ...appearance, textSize: 'unbounded' })).toBe(false)
  })
  it('persists and applies font size without changing other preferences', async () => {
    const ui = useUIStore(), old = { ...ui.appearance }
    ui.setAppearance({ textSize: 'large' }); await flushPromises()
    expect(ui.appearance).toEqual({ ...old, textSize: 'large' })
    expect(document.documentElement.dataset.textSize).toBe('large')
    expect(JSON.parse(localStorage.getItem('maigokite_inkline') || '{}').textSize).toBe('large')
  })
  it('searches translated labels and keywords without searching secret values', () => {
    expect(searchSettings('font size').map(field => field.id)).toEqual(['text-size'])
    expect(searchSettings('Tavily')[0]?.id).toBe('search-tools')
    expect(searchSettings('runtime').length).toBe(0)
    expect(searchSettings('')).toHaveLength(0)
  })
  it('retains the workspace deep link but names its actual runtime content', async () => {
    vi.spyOn(api, 'getWorkspaceRuntimeStatus').mockRejectedValue(new Error('unavailable'))
    const wrapper = mount(Settings, { global: { plugins: [await testRouter('/settings?tab=workspace')] } })
    await flushPromises()
    expect(wrapper.get('.settings-tabs [aria-pressed="true"]').text()).toBe('运行状态')
    expect(wrapper.get('.runtime-settings').text()).toContain('unavailable')
  })
  it('routes and focuses a settings search result with the keyboard', async () => {
    const router = await testRouter('/settings')
    const wrapper = mount(Settings, { attachTo: document.body, global: { plugins: [router] } })
    await flushPromises()
    await wrapper.get('.settings-search input').setValue('font')
    await wrapper.get('.settings-search input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(router.currentRoute.value.query.field).toBe('text-size')
    expect(document.activeElement).toBe(wrapper.get('[data-settings-field="text-size"] button[role="combobox"]').element)
    wrapper.unmount()
  })
  it('keeps an unsuccessful assistant-name draft and retries in place', async () => {
    const store = useSettingsStore(); store.profile.assistantName = '小七'
    const save = vi.spyOn(api, 'updateUserPreferences').mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ assistantName: '新名字' })
    const wrapper = mount(AppearanceSettingsPanel)
    await wrapper.get('#assistant-name').setValue('新名字')
    await wrapper.get('[data-settings-field="assistant-name"] .secondary-button').trigger('click'); await flushPromises()
    expect(wrapper.get('.field-error').text()).toContain('offline')
    expect((wrapper.get('#assistant-name').element as HTMLInputElement).value).toBe('新名字')
    await wrapper.get('.field-error button').trigger('click'); await flushPromises()
    expect(save).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.field-saved').text()).toBe('已保存')
  })
})
describe('final secrets and tool presentation', () => {
  it('masks credentials and never copies an absent saved key', async () => {
    const copy = vi.fn(); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: copy } })
    const wrapper = mount(SecretField, { props: { modelValue: '', label: 'Key' } })
    expect(wrapper.get('input').attributes('type')).toBe('password')
    const button = wrapper.get('[aria-label="复制 API Key"]')
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click'); expect(copy).not.toHaveBeenCalled()
  })
  it('reports a denied clipboard without claiming success', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    const wrapper = mount(SecretField, { props: { modelValue: 'new-value', label: 'Key' } })
    await wrapper.get('[aria-label="复制 API Key"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('无法复制')
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
  })
  it('releases credential feedback timers when unmounted', async () => {
    vi.useFakeTimers()
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
    const wrapper = mount(SecretField, { props: { modelValue: 'new-value', label: 'Key' } })
    await wrapper.get('[aria-label="复制 API Key"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[role="status"]').text()).toBe('已复制')
    wrapper.unmount(); expect(vi.getTimerCount()).toBe(0)
  })
  it('puts model-facing tool instructions in details, not in the user summary', async () => {
    const tools: ToolSetting[] = [{ name:'custom_tool',description:'MODEL PROTOCOL INSTRUCTIONS',source:'builtin',enabled:true,configurable:true,default_enabled:true }]
    vi.spyOn(api, 'getToolSettings').mockResolvedValue({ tools })
    const wrapper = mount(ToolSettingsPanel, { global: { plugins: [await testRouter('/settings')] } }); await flushPromises()
    expect(wrapper.get('.tool-row').text()).toContain('暂无说明')
    expect(wrapper.get('.tool-row').text()).not.toContain('MODEL PROTOCOL')
    expect(wrapper.get('details pre').text()).toBe('MODEL PROTOCOL INSTRUCTIONS')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
  })
  it('preserves failed search credentials for retry and clears them only after success', async () => {
    const save = vi.spyOn(api, 'saveCredential').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined)
    const wrapper = mount(SearchToolSettingsPanel, {
      attachTo: document.body,
      global: { plugins: [await testRouter('/settings')] },
    })
    await wrapper.get('.search-settings > .card-header').trigger('click')
    await wrapper.findAll('.search-provider > .card-header')[0].trigger('click')
    expect(wrapper.get('.credential-form input').isVisible()).toBe(true)
    await wrapper.get('.credential-form input').setValue('new-search-key')
    await wrapper.get('.credential-form .primary-button').trigger('click'); await flushPromises()
    expect(save).toHaveBeenNthCalledWith(1, 'tavily', 'new-search-key')
    expect(wrapper.get('.field-error').text()).toContain('offline')
    expect((wrapper.get('.credential-form input').element as HTMLInputElement).value).toBe('new-search-key')
    await wrapper.get('.field-error button').trigger('click'); await flushPromises()
    expect(save).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenNthCalledWith(2, 'tavily', 'new-search-key')
    expect((wrapper.get('.credential-form input').element as HTMLInputElement).value).toBe('')
    expect(wrapper.get('.field-saved').text()).toBe('已保存')
  })
  it('does not remove a credential when confirmation is declined', async () => {
    useSettingsStore().credentials.tavily = { service: 'tavily', configured: true }
    mount(InkConfirm)
    const remove = vi.spyOn(api, 'deleteCredential').mockResolvedValue(undefined)
    const wrapper = mount(SearchToolSettingsPanel, {
      attachTo: document.body,
      global: { plugins: [await testRouter('/settings')] },
    })
    await wrapper.get('.search-settings > .card-header').trigger('click')
    await wrapper.findAll('.search-provider > .card-header')[0].trigger('click')
    expect(wrapper.get('.credential-form .danger-button').isVisible()).toBe(true)
    await wrapper.get('.credential-form .danger-button').trigger('click')
    await flushPromises()
    const dialog = document.querySelector<HTMLDialogElement>('dialog.ink-confirm')!
    expect(dialog.open).toBe(true)
    expect(remove).not.toHaveBeenCalled()
    dialog.querySelector<HTMLButtonElement>('.secondary-button')!.click()
    await flushPromises()
    expect(dialog.open).toBe(false)
    expect(remove).not.toHaveBeenCalled()
    expect(useSettingsStore().credentials.tavily?.configured).toBe(true)
  })
})
describe('final list accessibility', () => {
  it('navigates provider model options without toggling selection until activated', async () => {
    const wrapper = mount(ModelCatalogPicker, { attachTo: document.body, props: { models:[{id:'a'},{id:'b'}],selectedModels:[],loading:false,canRefresh:true } })
    await wrapper.get('input').trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(wrapper.findAll('.model-option')[0].element)
    await wrapper.findAll('.model-option')[0].trigger('keydown', { key: 'End' })
    expect(document.activeElement).toBe(wrapper.findAll('.model-option')[1].element)
    expect(wrapper.emitted('select')).toBeUndefined()
    await wrapper.findAll('.model-option')[1].trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['b']); wrapper.unmount()
  })
  it('uses one roving tree tab stop and retains real expand/select events', async () => {
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{} as DOMRect] as unknown as DOMRectList)
    const wrapper = mount(FileTree, { attachTo: document.body, props: { nodes:[{name:'a',path:'a',type:'directory',expanded:false},{name:'b',path:'b',type:'file'}] } }); await flushPromises()
    const rows = wrapper.findAll('[role="treeitem"]')
    expect(rows.filter(row => row.attributes('tabindex') === '0')).toHaveLength(1)
    await rows[0].trigger('keydown', { key:'ArrowRight' })
    expect(wrapper.emitted('expand')?.[0]?.[0]).toEqual(expect.objectContaining({path:'a'}))
    await rows[0].trigger('keydown', { key:'ArrowDown' })
    expect(document.activeElement).toBe(rows[1].element)
    await rows[1].trigger('keydown', { key:'Enter' })
    expect(wrapper.emitted('select')?.[0]?.[0]).toEqual(expect.objectContaining({path:'b'})); wrapper.unmount()
  })
  it('keeps audit actors unknown when the event provides no actor, and filters real targets', async () => {
    vi.spyOn(api, 'listAuditEvents').mockResolvedValue([{id:'a',created_at:'2026-10-11T00:00:00Z',action:'read',resource:'notes',result:'allowed',details_json:'{}'},{id:'b',created_at:'2026-10-11T00:00:00Z',action:'write',resource:'config',result:'allowed',details_json:'{"actor":"nanami"}'}])
    const wrapper = mount(AuditLog, { global: { plugins:[await testRouter('/audit')] } }); await flushPromises()
    expect(wrapper.text()).toContain('未记录')
    await wrapper.get('input').setValue('nanami')
    expect(wrapper.findAll('.audit-timeline li')).toHaveLength(1)
    expect(wrapper.get('.audit-timeline').text()).toContain('config')
  })
})
