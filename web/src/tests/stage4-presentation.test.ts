import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type Conversation, type UserMemory } from '@/api/client'
import type { Turn } from '@/api/events'
import { i18n } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { useMemoryStore } from '@/stores/memory'
import { useSessionStore } from '@/stores/session'
import { paperLight, starterShortcut, takeWelcomeIntroduction } from '@/utils/welcome-presentation'
import { confidencePercent, knownSessionStatus, memoryGroup } from '@/utils/work-records'
import WelcomeSheet from '@/components/chat-area/WelcomeSheet.vue'
import Memory from '@/views/Memory.vue'
import InkConfirm from '@/components/common/InkConfirm.vue'
import Overview from '@/views/Overview.vue'
import { mockDialogs, testRouter } from './ui-harness'

const folder = { id: '', name: 'Default', root_path: '/work' }
function entry(content = '中文回答', id = 'm'): UserMemory {
  return {
    id,
    category: 'preference',
    content,
    confidence: 0.8,
    workspace_id: 'personal',
    subject_user_id: 'local',
    source_type: 'user',
    source_id: 'local',
    created_at: '',
    updated_at: '',
  }
}
function conversation(): Conversation {
  return {
    id: 'c',
    title: 'Record',
    folder_id: '',
    workspace_id: 'personal',
    model_id: '',
    response_mode: 'balanced',
    permission_mode: 'workspace',
    created_at: '',
    updated_at: '2026-10-11T08:00:00Z',
    message_count: 2,
  }
}
beforeEach(() => {
  mockDialogs()
  vi.spyOn(api, 'listWorkspaceFolders').mockResolvedValue([folder])
  vi.spyOn(api, 'getUserPreferences').mockResolvedValue({ assistantName: '小七' })
  vi.spyOn(api, 'listMemories').mockResolvedValue([])
  vi.spyOn(api, 'getMemorySettings').mockResolvedValue({ folder_id: '', enabled: false })
  vi.spyOn(api, 'getCapabilities').mockResolvedValue({ scoped_memory: true })
})
describe('stage 4 welcome', () => {
  it('keeps default workspace with empty id usable; starters only emit a draft', async () => {
    const wrapper = mount(WelcomeSheet, {
      props: { greeting: '早上好', quote: '固定语录' },
      global: { plugins: [await testRouter('/chat')] },
    })
    await flushPromises()
    const buttons = wrapper.findAll('.workpaper-starters button')
    expect(buttons).toHaveLength(4)
    expect(buttons[0].attributes('disabled')).toBeUndefined()
    await buttons[0].trigger('click')
    expect(wrapper.emitted('draft')?.[0]?.[0]).toContain('阅读当前工作区')
    expect(wrapper.emitted('send')).toBeUndefined()
    await wrapper.setProps({ greeting: 'Good morning' })
    expect(wrapper.get('.workpaper-epigraph p').text()).toBe('固定语录')
  })
  it('disables only file-dependent starters without a workspace', async () => {
    vi.mocked(api.listWorkspaceFolders).mockResolvedValue([])
    const wrapper = mount(WelcomeSheet, {
      props: { greeting: 'Hello', quote: 'Quote' },
      global: { plugins: [await testRouter('/chat')] },
    })
    await flushPromises()
    expect(
      wrapper
        .findAll('.workpaper-starters button')
        .map((button) => button.attributes('disabled') !== undefined),
    ).toEqual([true, false, true, false])
    expect(wrapper.text()).toContain('选择工作区后可用')
  })
  it('supports 1–4 without stealing input, modifiers, IME, or modal keys; removes listener', async () => {
    const onDraft = vi.fn()
    const wrapper = mount(WelcomeSheet, {
      props: { greeting: 'Hello', quote: '', onDraft },
      global: { plugins: [await testRouter('/chat')] },
    })
    await flushPromises()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '2', cancelable: true }))
    expect(onDraft).toHaveBeenCalledTimes(1)
    const input = document.createElement('textarea')
    document.body.append(input)
    const event = new KeyboardEvent('keydown', { key: '1', bubbles: true })
    input.dispatchEvent(event)
    expect(starterShortcut(event)).toBeUndefined()
    expect(
      starterShortcut(new KeyboardEvent('keydown', { key: '1', isComposing: true })),
    ).toBeUndefined()
    expect(
      starterShortcut(new KeyboardEvent('keydown', { key: '1', ctrlKey: true })),
    ).toBeUndefined()
    const dialog = document.createElement('dialog')
    dialog.open = true
    document.body.append(dialog)
    expect(starterShortcut(new KeyboardEvent('keydown', { key: '1' }))).toBeUndefined()
    dialog.remove()
    input.remove()
    wrapper.unmount()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '2' }))
    expect(onDraft).toHaveBeenCalledTimes(1)
  })
  it('shows workspace errors rather than claiming no folder', async () => {
    vi.mocked(api.listWorkspaceFolders).mockRejectedValue(new Error('offline'))
    const wrapper = mount(WelcomeSheet, {
      props: { greeting: 'Hello', quote: '' },
      global: { plugins: [await testRouter('/chat')] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('工作区读取失败')
  })
  it('buckets four light periods and consumes introduction only once', () => {
    expect([3, 8, 13, 21].map(paperLight)).toEqual(['night', 'morning', 'noon', 'evening'])
    expect(takeWelcomeIntroduction()).toBe(true)
    expect(takeWelcomeIntroduction()).toBe(false)
  })
  it('has translated starter and state copy', async () => {
    i18n.global.locale.value = 'en'
    const wrapper = mount(WelcomeSheet, {
      props: { greeting: 'Hello', quote: '' },
      global: { plugins: [await testRouter('/chat')] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Read the workspace')
    expect(wrapper.text()).toContain('Plan a task')
  })
})
describe('stage 4 memory', () => {
  it('retries failed name save without losing the edited name', async () => {
    const save = vi
      .spyOn(api, 'updateUserPreferences')
      .mockRejectedValueOnce(new Error('save failed'))
      .mockResolvedValue({ assistantName: '新称呼' })
    const wrapper = mount(Memory, { global: { plugins: [await testRouter('/memory')] } })
    await flushPromises()
    await wrapper.get('.identity-profile label input').setValue('新称呼')
    await wrapper.get('.identity-profile > button').trigger('click')
    await flushPromises()
    expect(wrapper.get('.identity-profile .memory-error').text()).toContain('save failed')
    await wrapper.get('.identity-profile .memory-error button').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenLastCalledWith({ assistantName: '新称呼' })
    expect(wrapper.get('.memory-notice').text()).toBe('已记下')
  })
  it('clears save feedback timers when leaving the page', async () => {
    vi.useFakeTimers()
    vi.spyOn(api, 'updateUserPreferences').mockResolvedValue({ assistantName: '小七' })
    const wrapper = mount(Memory, { global: { plugins: [await testRouter('/memory')] } })
    await flushPromises()
    const scheduled = vi.spyOn(globalThis, 'setTimeout')
    const cleared = vi.spyOn(globalThis, 'clearTimeout')
    await wrapper.get('.identity-profile > button').trigger('click')
    await flushPromises()
    const index = scheduled.mock.calls.findIndex(([, delay]) => delay === 2200)
    expect(index).toBeGreaterThanOrEqual(0)
    const ownedTimer = scheduled.mock.results[index].value
    wrapper.unmount()
    expect(cleared).toHaveBeenCalledWith(ownedTimer)
  })
  it('preserves persona prefix precedence and normalized confidence display', () => {
    expect(memoryGroup(entry('人设：温和'))).toBe('persona')
    expect(memoryGroup(entry())).toBe('preference')
    expect([-0.1, 0.825, 1.4, NaN].map(confidencePercent)).toEqual([0, 83, 100, 0])
  })
  it('creates persona inline through the existing API and reports real success', async () => {
    const saved = vi.spyOn(api, 'saveMemory').mockResolvedValue(entry('人设：温和'))
    const wrapper = mount(Memory, { global: { plugins: [await testRouter('/memory')] } })
    await flushPromises()
    await wrapper.get('.memory-identity .memory-group header button').trigger('click')
    await wrapper.get('.memory-editor textarea').setValue('温和')
    await wrapper.get('.memory-editor').trigger('submit')
    await flushPromises()
    expect(saved).toHaveBeenCalledWith(
      expect.objectContaining({ content: '人设：温和', category: 'fact', confidence: 1 }),
    )
    expect(wrapper.get('.memory-notice').text()).toBe('已记下')
    expect(wrapper.find('.memory-editor').exists()).toBe(false)
  })
  it('keeps draft on save failure; retry reloads existing query', async () => {
    vi.spyOn(api, 'saveMemory').mockRejectedValue(new Error('save failed'))
    const wrapper = mount(Memory, { global: { plugins: [await testRouter('/memory')] } })
    await flushPromises()
    await wrapper.get('.memory-columns .memory-group header button').trigger('click')
    await wrapper.get('.memory-editor textarea').setValue('Keep this')
    await wrapper.get('.memory-editor').trigger('submit')
    await flushPromises()
    expect((wrapper.get('.memory-editor textarea').element as HTMLTextAreaElement).value).toBe(
      'Keep this',
    )
    expect(wrapper.get('.memory-notice').text()).toBe('')
    await wrapper.get('.memory-error button').trigger('click')
    await flushPromises()
    expect(api.listMemories).toHaveBeenCalledTimes(2)
  })
  it('edits in place with category/confidence unchanged and keeps search-filtered save feedback', async () => {
    vi.mocked(api.listMemories).mockResolvedValue([entry()])
    vi.spyOn(api, 'saveMemory').mockResolvedValue(entry('new wording'))
    const wrapper = mount(Memory, { global: { plugins: [await testRouter('/memory')] } })
    await flushPromises()
    const memory = useMemoryStore()
    memory.query = '中文'
    await wrapper.get('.memory-item footer button:nth-child(2)').trigger('click')
    expect(wrapper.find('.memory-item .memory-editor').exists()).toBe(true)
    await wrapper.get('.memory-editor textarea').setValue('new wording')
    await wrapper.get('.memory-editor').trigger('submit')
    await flushPromises()
    expect(api.saveMemory).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'm', category: 'preference', confidence: 0.8 }),
    )
    expect(wrapper.find('.memory-item').exists()).toBe(false)
    expect(wrapper.get('.memory-notice').text()).toBe('已记下')
  })
  it('requires confirmation before deleting and preserves pin action', async () => {
    vi.mocked(api.listMemories).mockResolvedValue([entry()])
    const deletion = vi.spyOn(api, 'deleteMemory').mockResolvedValue(undefined)
    mount(InkConfirm)
    const wrapper = mount(Memory, { global: { plugins: [await testRouter('/memory')] } })
    await flushPromises()
    await wrapper.get('.memory-item footer button').trigger('click')
    expect(useMemoryStore().pinnedIds).toContain('m')
    await wrapper.get('.memory-item .danger').trigger('click')
    expect(deletion).not.toHaveBeenCalled()
    await flushPromises()
    const dialog = document.querySelector<HTMLDialogElement>('dialog.ink-confirm')!
    expect(dialog.open).toBe(true)
    expect(dialog.textContent).toContain('遗忘这条记忆？')
    dialog.querySelector<HTMLButtonElement>('.secondary-button')!.click()
    await flushPromises()
    expect(dialog.open).toBe(false)
    expect(deletion).not.toHaveBeenCalled()
    expect(wrapper.find('.memory-item').exists()).toBe(true)
    await wrapper.get('.memory-item .danger').trigger('click')
    await flushPromises()
    expect(dialog.open).toBe(true)
    dialog.querySelector<HTMLButtonElement>('.danger-button')!.click()
    await flushPromises()
    expect(deletion).toHaveBeenCalledWith('m')
    expect(deletion).toHaveBeenCalledTimes(1)
    expect(dialog.open).toBe(false)
  })
  it('retains focus query highlighting and accessible confidence', async () => {
    vi.mocked(api.listMemories).mockResolvedValue([entry()])
    const wrapper = mount(Memory, {
      attachTo: document.body,
      global: { plugins: [await testRouter('/memory?focus=m')] },
    })
    await flushPromises()
    expect(wrapper.get('#memory-m').classes()).toContain('focused-memory')
    expect(document.activeElement?.id).toBe('memory-m')
    expect(wrapper.get('[role="meter"]').attributes('aria-valuenow')).toBe('80')
    expect(api.listMemories).toHaveBeenCalledWith('', '')
    wrapper.unmount()
  })
  it('provides local empty CTAs and error retry, not a false empty state', async () => {
    vi.mocked(api.listMemories).mockRejectedValue(new Error('offline'))
    const wrapper = mount(Memory, { global: { plugins: [await testRouter('/memory')] } })
    await flushPromises()
    expect(wrapper.findAll('.memory-empty')).toHaveLength(0)
    vi.mocked(api.listMemories).mockResolvedValue([])
    await wrapper.get('.memory-error button').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.memory-empty button')).toHaveLength(3)
  })
})
describe('stage 4 records', () => {
  it('never invents completion from an unloaded session', () => {
    expect(knownSessionStatus([], [])).toBeUndefined()
    expect(knownSessionStatus([], ['t'])).toBe('running')
    expect(knownSessionStatus([{ status: 'failed' } as Turn], [])).toBe('failed')
    expect(knownSessionStatus([{ id: 'old', status: 'completed' } as Turn], ['new'])).toBe(
      'running',
    )
  })
  it('keeps confirmed empty records on the page with a note and chat entry', async () => {
    vi.spyOn(api, 'listConversations').mockResolvedValue([])
    const router = await testRouter('/overview')
    const wrapper = mount(Overview, { global: { plugins: [router] } })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/overview')
    expect(wrapper.text()).toContain('还没有对话')
    await wrapper.get('.record-new').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/chat')
  })
  it('does not redirect on errors or archived-only records', async () => {
    vi.spyOn(api, 'listConversations').mockRejectedValue(new Error('offline'))
    const router = await testRouter('/overview')
    const wrapper = mount(Overview, { global: { plugins: [router] } })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/overview')
    vi.mocked(api.listConversations).mockResolvedValue([{ ...conversation(), archived: true }])
    await wrapper.get('.record-error button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/overview')
    expect(wrapper.text()).toContain('对话已归档')
  })
  it('uses real recent rows, workspace path and unknown state; no metrics or greeting', async () => {
    vi.spyOn(api, 'listConversations').mockResolvedValue([conversation()])
    const wrapper = mount(Overview, { global: { plugins: [await testRouter('/overview')] } })
    await flushPromises()
    expect(wrapper.text()).toContain('/work')
    expect(wrapper.text()).toContain('状态尚未读取')
    expect(wrapper.find('.metrics').exists()).toBe(false)
    expect(wrapper.find('.hero').exists()).toBe(false)
    expect(useWorkspaceStore().current?.id).toBe('')
    expect(useSessionStore().sessions).toHaveLength(1)
  })
})
