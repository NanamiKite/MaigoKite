import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import SearchToolSettingsPanel from '@/components/settings/SearchToolSettingsPanel.vue'
import { useSettingsStore } from '@/stores/settings'
import { testRouter } from './ui-harness'

describe('search tool settings', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
  })

  it('saves Tavily through the credential reference API without echoing the secret', async () => {
    const save = vi.spyOn(api, 'saveCredential').mockResolvedValue()
    const store = useSettingsStore()
    // Document attachment lets jsdom invalidate computed styles after v-show changes.
    const wrapper = mount(SearchToolSettingsPanel, {
      attachTo: document.body,
      global: { plugins: [await testRouter('/settings')] },
    })

    expect(wrapper.get('.search-settings > .card-header').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('input[type="password"]').isVisible()).toBe(false)
    await wrapper.get('.search-settings > .card-header').trigger('click')
    await wrapper.findAll('.search-provider > .card-header')[0].trigger('click')
    expect(wrapper.get('input[type="password"]').isVisible()).toBe(true)

    await wrapper.find('input[type="password"]').setValue('tvly-secret-key')
    await wrapper.find('.credential-form .primary-button').trigger('click')
    await flushPromises()

    expect(save).toHaveBeenCalledWith('tavily', 'tvly-secret-key')
    expect(store.credentials.tavily.configured).toBe(true)
    expect((wrapper.find('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.html()).not.toContain('tvly-secret-key')
  })

  it('stores Google key and CX as one opaque credential', async () => {
    const save = vi.spyOn(api, 'saveCredential').mockResolvedValue()
    const wrapper = mount(SearchToolSettingsPanel, {
      attachTo: document.body,
      global: { plugins: [await testRouter('/settings')] },
    })
    expect(wrapper.get('.google-form').isVisible()).toBe(false)
    await wrapper.get('.search-settings > .card-header').trigger('click')
    await wrapper.findAll('.search-provider > .card-header')[1].trigger('click')
    expect(wrapper.get('.google-form').isVisible()).toBe(true)
    const fields = wrapper.findAll('.google-form input')

    await fields[0].setValue('google-secret-key')
    await fields[1].setValue('custom-search-id')
    await wrapper.get('.google-form .primary-button').trigger('click')
    await flushPromises()

    expect(save).toHaveBeenCalledWith('google', 'google-secret-key:custom-search-id')
  })

  it('expands the search forms when opened through the settings search deep link', async () => {
    const wrapper = mount(SearchToolSettingsPanel, {
      attachTo: document.body,
      global: { plugins: [await testRouter('/settings?field=search-tools')] },
    })

    expect(wrapper.get('.search-settings > .card-header').attributes('aria-expanded')).toBe('true')
    expect(wrapper.findAll('.search-provider > .card-header').map(header => header.attributes('aria-expanded')))
      .toEqual(['true', 'true'])
    expect(wrapper.get('.credential-form input').isVisible()).toBe(true)
    expect(wrapper.get('.google-form').isVisible()).toBe(true)
  })

  it('does not expose credential-storage implementation details', async () => {
    const wrapper = mount(SearchToolSettingsPanel, { global: { plugins: [await testRouter('/settings')] } })

    expect(wrapper.text()).not.toContain('操作系统密钥链')
    expect(wrapper.text()).not.toContain('Secret')
  })
})
