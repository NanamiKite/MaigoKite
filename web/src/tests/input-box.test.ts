import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { chooseOption, mockDialogs, testRouter } from './ui-harness'
import { api } from '@/api/client'

import InputBox from '@/components/chat-area/InputBox.vue'

describe('InputBox', () => {
  const configuredModel = [{ id: 'model-1', name: 'Local', model: 'qwen' }]
  let global: { plugins: Awaited<ReturnType<typeof testRouter>>[] }
  beforeEach(async () => {
    global = { plugins: [await testRouter('/chat/conv-1')] }
    mockDialogs()
    vi.spyOn(api, 'getModelThinking').mockResolvedValue({
      kind: 'effort', options: ['low', 'medium', 'high'], default: 'medium', source: 'fixture', reason: '',
    })
    vi.spyOn(api, 'getModelContext').mockResolvedValue({
      seq: 0, callId: '', modelId: 'model-1', modelName: 'qwen',
      usedTokens: null, windowTokens: 8192, measurement: null,
      capacitySource: 'model_profile', capacityStatus: 'known',
    })
  })

  it('trims and emits non-empty messages', async () => {
    const wrapper = mount(InputBox, {
      global,
      props: { sessionId: 'conv-1', modelId: 'model-1', modelOptions: configuredModel },
    })
    await wrapper.get('textarea').setValue('  hello  ')
    await wrapper.get('button[aria-label="发送消息"]').trigger('click')
    expect(wrapper.emitted('send')?.[0]).toEqual(['hello'])
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('sends with Enter but keeps Shift+Enter for a new line', async () => {
    const wrapper = mount(InputBox, {
      global,
      props: { sessionId: 'conv-1', modelId: 'model-1', modelOptions: configuredModel },
    })
    const textarea = wrapper.get('textarea')

    await textarea.setValue('first line')
    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeUndefined()

    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')?.[0]).toEqual(['first line'])
  })

  it('uses the configured assistant name in its placeholder', () => {
    const wrapper = mount(InputBox, {
      global,
      props: {
        sessionId: 'conv-1',
        assistantName: '小七',
        modelId: 'model-1',
        modelOptions: configuredModel,
      },
    })
    expect(wrapper.get('textarea').attributes('placeholder')).toBe('问问 小七…')
  })

  it('does not put execution progress in the composer while sending', () => {
    const wrapper = mount(InputBox, {
      global,
      props: {
        sessionId: 'conv-1',
        assistantName: '小七',
        isSending: true,
        modelId: 'model-1',
        modelOptions: configuredModel,
      },
    })
    expect(wrapper.get('textarea').attributes('placeholder')).toBe('问问 小七…')
    expect(wrapper.get('.composer').attributes('aria-busy')).toBe('true')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('turns the send control into an explicit stop action while running', async () => {
    const wrapper = mount(InputBox, {
      global,
      props: {
        sessionId: 'conv-1',
        isSending: true,
        modelId: 'model-1',
        modelOptions: configuredModel,
      },
    })
    const button = wrapper.get('button[aria-label="停止任务"]')
    expect(button.attributes('aria-label')).toBe('停止任务')
    await button.trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('emits model, capability-based thinking and approval-mode choices', async () => {
    const wrapper = mount(InputBox, {
      global,
      props: { sessionId: 'conv-1', modelId: 'model-1', permissionMode: 'read_only', modelOptions: [...configuredModel, { id: 'model-2', name: 'Cloud', model: 'other' }] },
    })
    await wrapper.get('[aria-haspopup="dialog"]').trigger('click')
    await flushPromises()
    const dialog = document.querySelector('dialog[aria-label="选择对话模型"]') as HTMLDialogElement
    expect(dialog.open).toBe(true)
    const option = dialog.querySelector<HTMLButtonElement>('[role="treeitem"][aria-level="2"][data-node-id="model-2"]')!
    expect(option).toBeDefined()
    option.click()
    await flushPromises()
    expect(dialog.open).toBe(false)
    await wrapper.get('input[aria-label="思考强度"]').setValue('3')
    await chooseOption(wrapper, '执行权限', '工作区')
    expect(wrapper.emitted('model-change')?.[0]).toEqual(['model-2'])
    expect(wrapper.emitted('thinking-change')).toEqual([['high']])
    expect(wrapper.emitted('permission-mode-change')).toEqual([['workspace']])
  })
})
