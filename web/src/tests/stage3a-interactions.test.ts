import { mount, flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import InputBox from '@/components/chat-area/InputBox.vue'
import SupplementMessage from '@/components/chat-area/SupplementMessage.vue'
import QueuedMessages from '@/components/chat-area/QueuedMessages.vue'
import JobsTab from '@/components/drawer/JobsTab.vue'
import PlanTab from '@/components/drawer/PlanTab.vue'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/session'
import { useRunStore } from '@/stores/run'
import { api } from '@/api/client'
const models = [{ id: 'm', name: 'Local', model: 'local' }]
const global = { plugins: [createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })], stubs: { ModelPicker: true, ComposerStats: true, ModePill: true, SubagentSettings: true, MentionMenu: true, SlashMenu: true } }
describe('3a composer and drawer interactions', () => {
  it('queues with send shortcuts, guides only with send now, and stops independently', async () => {
    const submit = vi.fn().mockResolvedValue(undefined)
    const wrapper = mount(InputBox, { props: { sessionId: 'c', isSending: true, modelOptions: models, submit }, global })
    const textarea = wrapper.get('textarea')
    await textarea.setValue('new line')
    await textarea.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(submit).not.toHaveBeenCalled()
    expect((textarea.element as HTMLTextAreaElement).value).toBe('new line')
    const shortcuts = [
      { key: 's', code: 'KeyS', altKey: true },
      { key: 'Enter', ctrlKey: true },
      { key: 'Enter', metaKey: true },
      { key: 'Enter', altKey: true },
    ]
    for (const [index, shortcut] of shortcuts.entries()) {
      await textarea.setValue(`later ${index}`)
      await textarea.trigger('keydown', shortcut)
      await flushPromises()
      expect(submit).toHaveBeenCalledTimes(index + 1)
      expect(submit).toHaveBeenLastCalledWith(`later ${index}`, 'later')
    }
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await textarea.setValue('read only')
    await wrapper.get('[aria-label="发送选项"]').trigger('click')
    const guide = wrapper.findAll('.send-options-menu button').find(button => button.text() === '立即发送')
    expect(guide).toBeDefined()
    await guide!.trigger('click')
    await flushPromises()
    expect(submit).toHaveBeenCalledTimes(5)
    expect(submit).toHaveBeenLastCalledWith('read only', 'guide')
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await textarea.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(submit).toHaveBeenCalledTimes(5)
  })
  it('retains the pending draft on submission refusal and permits an explicit retry', async () => {
    let rejectRequest: ((reason: Error) => void) | undefined
    const submit = vi.fn().mockImplementationOnce(() => new Promise<void>((_resolve, fail) => {
      rejectRequest = fail
    })).mockResolvedValue(undefined)
    const wrapper = mount(InputBox, { props: { sessionId: 'c', isSending: true, modelOptions: models, submit }, global })
    await wrapper.get('textarea').setValue('do not lose me')
    await wrapper.get('textarea').trigger('keydown', { key: 's', code: 'KeyS', altKey: true })
    expect(submit).toHaveBeenCalledTimes(1)
    expect(submit).toHaveBeenLastCalledWith('do not lose me', 'later')
    expect(wrapper.get('[aria-label="正在发送消息"]').attributes('disabled')).toBeDefined()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('do not lose me')
    if (!rejectRequest) throw new Error('The submission must start before testing its refusal')
    rejectRequest(new Error('too many pending messages'))
    await flushPromises()
    expect(submit).toHaveBeenCalledTimes(1)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('do not lose me')
    expect(wrapper.get('[role="alert"]').text()).toContain('too many')
    await wrapper.get('textarea').trigger('keydown', { key: 's', code: 'KeyS', altKey: true })
    await flushPromises()
    expect(submit).toHaveBeenCalledTimes(2)
    expect(submit).toHaveBeenLastCalledWith('do not lose me', 'later')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })
  it('does not erase a newer draft when an older submission succeeds', async () => {
    let resolveRequest: (() => void) | undefined
    const submit = vi.fn().mockImplementation(() => new Promise<void>(done => { resolveRequest = done }))
    const wrapper = mount(InputBox, { props: { sessionId: 'c', isSending: true, modelOptions: models, submit }, global })
    await wrapper.get('textarea').setValue('original')
    await wrapper.get('textarea').trigger('keydown', { key: 's', code: 'KeyS', altKey: true })
    expect(submit).toHaveBeenCalledTimes(1)
    expect(submit).toHaveBeenLastCalledWith('original', 'later')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('original')
    await wrapper.get('textarea').setValue('new draft')
    if (!resolveRequest) throw new Error('The submission must start before testing its acknowledgement')
    resolveRequest()
    await flushPromises()
    expect(submit).toHaveBeenCalledTimes(1)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('new draft')
    expect(wrapper.get('[aria-label="加入队列"]').attributes('disabled')).toBeUndefined()
  })
  it('disables steering while waiting but leaves send later available', async () => {
    const submit = vi.fn().mockResolvedValue(undefined)
    const wrapper = mount(InputBox, { props: { sessionId: 'c', isSending: true, waiting: true, guideReason: '请先处理审批', modelOptions: models, submit }, global })
    await wrapper.get('textarea').setValue('later')
    await wrapper.get('[aria-label="发送选项"]').trigger('click')
    const guide = wrapper.findAll('.send-options-menu button').find(button => button.text() === '立即发送')
    expect(guide).toBeDefined()
    expect(guide!.attributes('disabled')).toBeDefined()
    expect(guide!.attributes('title')).toBe('请先处理审批')
    await guide!.trigger('click')
    expect(submit).not.toHaveBeenCalled()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('later')
    await wrapper.get('textarea').trigger('keydown', { key: 's', code: 'KeyS', altKey: true })
    await flushPromises()
    expect(submit).toHaveBeenCalledTimes(1)
    expect(submit).toHaveBeenCalledWith('later', 'later')
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await wrapper.setProps({ waiting: false, guideReason: '' })
    await wrapper.get('textarea').setValue('guide after approval')
    await wrapper.get('[aria-label="发送选项"]').trigger('click')
    const enabledGuide = wrapper.findAll('.send-options-menu button').find(button => button.text() === '立即发送')
    expect(enabledGuide).toBeDefined()
    expect(enabledGuide!.attributes('disabled')).toBeUndefined()
    await enabledGuide!.trigger('click')
    await flushPromises()
    expect(submit).toHaveBeenCalledTimes(2)
    expect(submit).toHaveBeenLastCalledWith('guide after approval', 'guide')
  })
  it('explicitly shows unused input with a new-message action and no automatic resend', async () => {
    const resend = vi.fn().mockResolvedValue(undefined)
    const wrapper = mount(SupplementMessage, { props: { input: { id: 'i', task_id: 't', content: 'late', status: 'not_consumed', created_at: 'now' }, canResend: true, resend } })
    expect(wrapper.text()).toContain('本轮未采用')
    expect(wrapper.get('button').text()).toBe('作为新回合发送')
    expect(resend).not.toHaveBeenCalled()
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(resend).toHaveBeenCalledTimes(1)
  })
  it('keeps queued edits cancellable and exposes remove and guide actions', async () => {
    const guide = vi.fn().mockResolvedValue(undefined)
    const wrapper = mount(QueuedMessages, { props: { items: [{ id: 'q', content: 'original' }], paused: false, guideReason: '', canContinue: false, guide } })
    expect(guide).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '立即发送')!.trigger('click')
    await flushPromises()
    expect(guide).toHaveBeenCalledTimes(1)
    expect(guide).toHaveBeenCalledWith('q')
    expect(wrapper.emitted('remove')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '编辑')!.trigger('click')
    await wrapper.get('textarea').setValue('edited')
    await wrapper.findAll('button').find(button => button.text() === '保存')!.trigger('click')
    expect(wrapper.emitted('edit')?.[0]).toEqual(['q', 'edited'])
    await wrapper.findAll('button').find(button => button.text() === '移除')!.trigger('click')
    expect(wrapper.emitted('remove')?.[0]).toEqual([0])
  })
  it('keeps actual jobs visible and reveals historical input only after explicit expansion', async () => {
    const ui = useUIStore(), session = useSessionStore()
    ui.drawerOpen = true; ui.drawerTab = 'jobs'; session.activeSessionId = 'c'; session.capabilities.process_list = true
    const listTasks = vi.spyOn(api, 'listTasks').mockResolvedValue([{ id: 't', workspace_id: 'personal', conversation_id: 'c', title: 'Finished', input_text: 'full historical text must stay in chat', status: 'succeeded', version: 1, last_error: '', created_at: '2026-10-11T00:00:00Z', updated_at: '2026-10-11T00:00:01Z' }])
    const listJobs = vi.spyOn(api, 'listJobs').mockResolvedValue([{ id: 'j', task_id: 't', conversation_id: 'c', command: 'echo actual', status: 'completed', log_tail: 'actual output', live: false, live_logs_available: false, stdin_available: false }])
    const wrapper = mount(JobsTab)
    await flushPromises()
    const toggle = wrapper.get('button[aria-expanded="false"]')
    expect(toggle.text()).toContain('Finished')
    expect(wrapper.find('.entry-request').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('full historical text')
    expect(wrapper.text()).toContain('echo actual')
    expect(wrapper.text()).toContain('actual output')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.entry-request').text()).toBe('full historical text must stay in chat')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('.entry-request').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('full historical text')
    expect(listTasks).toHaveBeenCalledTimes(1)
    expect(listJobs).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
  it('renders a genuine plan empty state instead of invented steps', () => {
    useSessionStore().activeSessionId = 'c'
    useRunStore().markStarted('c', 't')
    const wrapper = mount(PlanTab)
    expect(wrapper.text()).toContain('还没有执行计划')
    expect(wrapper.findAll('li')).toHaveLength(0)
  })
  it('closes the send menu with Esc without stopping the task', async () => {
    const wrapper = mount(InputBox, { props: { sessionId: 'c', isSending: true, modelOptions: models, submit: vi.fn() }, global })
    await wrapper.get('[aria-label="发送选项"]').trigger('click')
    expect(wrapper.find('.send-options-menu').exists()).toBe(true)
    await wrapper.get('.send-options button').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('.send-options-menu').exists()).toBe(false)
    expect(wrapper.emitted('cancel')).toBeUndefined()
  })
})
