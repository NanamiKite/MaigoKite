import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import PermissionSettingsPanel from '@/components/settings/PermissionSettingsPanel.vue'
import { useSettingsStore } from '@/stores/settings'

describe('permission settings', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
    vi.spyOn(api, 'getCapabilities').mockResolvedValue({ permission_boundary_v2: true })
  })

  it('shows three clear profiles and keeps internal risk baselines out of the UI', async () => {
    const store = useSettingsStore()
    store.permissions = { default_mode: 'workspace' }
    vi.spyOn(api, 'updatePermissionSettings').mockImplementation(async (value) => value)
    const wrapper = mount(PermissionSettingsPanel)
    await flushPromises()

    for (const label of ['只读', '工作区', '完全访问', '推荐']) {
      expect(wrapper.text()).toContain(label)
    }
    expect(wrapper.text()).not.toContain('L1')
    await wrapper.findAll('.mode-grid button')[0].trigger('click')
    await wrapper.get('footer button').trigger('click')

    expect(api.updatePermissionSettings).toHaveBeenCalledWith({ default_mode: 'read_only' })
    expect(api.updatePermissionSettings).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('requires explicit acknowledgement only when enabling full access', async () => {
    const store = useSettingsStore()
    store.permissions = { default_mode: 'workspace' }
    vi.spyOn(api, 'updatePermissionSettings').mockImplementation(async (value) => value)
    const wrapper = mount(PermissionSettingsPanel)
    await flushPromises()

    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    await wrapper.findAll('.mode-grid button')[2].trigger('click')
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true)
    await wrapper.get('footer button').trigger('click')
    expect(api.updatePermissionSettings).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('请先确认完全访问的风险。')

    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('footer button').trigger('click')
    await flushPromises()
    expect(api.updatePermissionSettings).toHaveBeenCalledTimes(1)
    expect(api.updatePermissionSettings).toHaveBeenCalledWith({
      default_mode: 'danger_full_access',
    })
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
  })

  it('does not ask again when the saved default is already full access', async () => {
    const store = useSettingsStore()
    store.permissions = { default_mode: 'danger_full_access' }
    vi.spyOn(api, 'updatePermissionSettings').mockImplementation(async (value) => value)
    const wrapper = mount(PermissionSettingsPanel)
    await flushPromises()

    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    await wrapper.get('footer button').trigger('click')
    expect(api.updatePermissionSettings).toHaveBeenCalledWith({
      default_mode: 'danger_full_access',
    })
  })
})
