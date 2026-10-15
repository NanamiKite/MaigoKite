import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { api } from '@/api/client'
import SubagentSettingsPanel from '@/components/settings/SubagentSettingsPanel.vue'
import InkLoader from '@/components/common/InkLoader.vue'

describe('subagent settings loading ownership', () => {
  it('finishes its own request without clearing the parent loading guard', async () => {
    let resolveCapabilities: ((value: Record<string, boolean>) => void) | undefined
    const capabilities = vi.spyOn(api, 'getCapabilities').mockImplementation(() =>
      new Promise<Record<string, boolean>>(resolve => { resolveCapabilities = resolve }),
    )
    const wrapper = mount(SubagentSettingsPanel, { props: { loading: true } })
    expect(capabilities).toHaveBeenCalledTimes(1)
    await nextTick()
    expect(wrapper.findComponent(InkLoader).exists()).toBe(true)
    expect(wrapper.emitted('ready')).toBeUndefined()
    if (!resolveCapabilities) throw new Error('The capabilities request must start before it can finish')
    resolveCapabilities({ subagents: true, subagent_model_selection: true })
    await flushPromises()
    expect(wrapper.findComponent(InkLoader).exists()).toBe(false)
    expect(wrapper.emitted('ready')).toHaveLength(1)
    const save = wrapper.get('button.primary-button')
    expect(save.attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-settings-field="subagent-calls"] input').attributes('disabled')).toBeDefined()
    await save.trigger('click')
    expect(localStorage.getItem('maigokite_subagent_defaults')).toBeNull()

    await wrapper.setProps({ loading: false })
    const calls = wrapper.get('[data-settings-field="subagent-calls"] input')
    expect(calls.attributes('disabled')).toBeUndefined()
    await calls.setValue('2')
    expect(save.attributes('disabled')).toBeUndefined()
    await save.trigger('click')
    await flushPromises()
    expect(JSON.parse(localStorage.getItem('maigokite_subagent_defaults') || 'null')).toEqual({
      calls: 2, concurrent: 1, modelIds: [],
    })
    expect(wrapper.get('[role="status"]').text()).toBe('已保存')
    expect(capabilities).toHaveBeenCalledTimes(1)
  })

  it('clears its own failed load and permits an explicit retry', async () => {
    const capabilities = vi.spyOn(api, 'getCapabilities')
      .mockRejectedValueOnce(new Error('capabilities unavailable'))
      .mockResolvedValue({ subagents: true })
    const wrapper = mount(SubagentSettingsPanel)
    await flushPromises()
    expect(capabilities).toHaveBeenCalledTimes(1)
    expect(wrapper.findComponent(InkLoader).exists()).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toContain('capabilities unavailable')
    const retry = wrapper.get('[role="alert"] button')
    expect(retry.attributes('disabled')).toBeUndefined()
    await retry.trigger('click')
    await flushPromises()
    expect(capabilities).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('ready')).toHaveLength(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findComponent(InkLoader).exists()).toBe(false)
    expect(wrapper.get('button.primary-button').attributes('disabled')).toBeUndefined()
  })
})
