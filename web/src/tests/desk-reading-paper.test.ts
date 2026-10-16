import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { useDeskStore } from '@/stores/desk'
import { prepareDeskImage, type DeskImage } from '@/utils/desk-image'
import { emptyDesk } from '@/utils/desk-preferences'
import {
  deskPreferenceKey,
  getDeskImage,
  putDeskImage,
  deleteDeskImage,
} from '@/utils/desk-storage'
import type { DeskPalette } from '@/utils/desk-contrast'

vi.mock('@/utils/desk-image', () => ({ prepareDeskImage: vi.fn() }))
vi.mock('@/utils/desk-storage', async (original) => ({
  ...(await original<typeof import('@/utils/desk-storage')>()),
  getDeskImage: vi.fn(),
  putDeskImage: vi.fn(),
  deleteDeskImage: vi.fn(),
}))

const palette: DeskPalette = { paper: [248, 245, 239], dim: 0.25, pairs: [] }
const storedImage = (id = 'old-image'): DeskImage => ({
  id,
  width: 1,
  height: 1,
  clear: new Blob(['clear']),
  soft: new Blob(['soft']),
})
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
let pinia: ReturnType<typeof createPinia>
let store: ReturnType<typeof useDeskStore>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  store = useDeskStore()
  localStorage.clear()
  vi.mocked(getDeskImage).mockReset().mockResolvedValue(storedImage())
  vi.mocked(prepareDeskImage).mockReset().mockResolvedValue(storedImage('new-image'))
  vi.mocked(putDeskImage).mockReset().mockResolvedValue('new-image')
  vi.mocked(deleteDeskImage).mockReset().mockResolvedValue(undefined)
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn(() => 'blob:desk-test')
      static revokeObjectURL = vi.fn()
    },
  )
})
afterEach(() => {
  store.stop()
  disposePinia(pinia)
  vi.restoreAllMocks()
  localStorage.clear()
})

async function restoreImage() {
  localStorage.setItem(
    deskPreferenceKey,
    JSON.stringify({ ...emptyDesk(), on: true, imageId: 'old-image' }),
  )
  store.start()
  await flushPromises()
  expect(store.active).toBe(true)
}

describe('user-controlled reading paper', () => {
  it.each([0, 0.5, 1])(
    'persists literal transparency %s without hidden contrast clamping',
    (value) => {
      store.change({ paperTransparency: value })
      expect(store.paperTransparency).toBe(value)
      expect(store.paperAlpha).toBe(1 - value)
      expect(JSON.parse(localStorage.getItem(deskPreferenceKey)!)).toMatchObject({
        paperTransparency: value,
      })
    },
  )

  it('uses legacy visibility only until an explicit transparency has been saved', async () => {
    localStorage.setItem(deskPreferenceKey, JSON.stringify({ ...emptyDesk(), visibility: 0.4 }))
    store.start()
    await flushPromises()
    expect(store.paperTransparency).toBe(0.4)
    store.change({ paperTransparency: 0.8 })
    store.change({ visibility: 0.1 })
    expect(store.paperTransparency).toBe(0.8)
    expect(store.paperAlpha).toBeCloseTo(0.2)
  })

  it.each([-1, 1.01, NaN, Infinity])(
    'rejects invalid transparency %s without changing saved preferences',
    (value) => {
      store.change({ paperTransparency: 0.5 })
      const saved = localStorage.getItem(deskPreferenceKey)
      store.change({ paperTransparency: value })
      expect(store.paperAlpha).toBe(0.5)
      expect(localStorage.getItem(deskPreferenceKey)).toBe(saved)
    },
  )

  it('keeps the previous value and reports a failed preference write', () => {
    store.change({ paperTransparency: 0.5 })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    store.change({ paperTransparency: 1 })
    expect(store.paperTransparency).toBe(0.5)
    expect(store.notice).toBe('无法保存背景设置。')
  })

  it('restores an image and releases its URL on shutdown', async () => {
    await restoreImage()
    expect(getDeskImage).toHaveBeenCalledWith('old-image')
    expect(URL.createObjectURL).toHaveBeenCalledWith(store.image!.clear)
    store.stop()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:desk-test')
    expect(store.active).toBe(false)
    expect(store.image).toBeNull()
    window.dispatchEvent(new StorageEvent('storage', { key: deskPreferenceKey }))
    await flushPromises()
    expect(getDeskImage).toHaveBeenCalledTimes(1)
  })

  it('does not install a late restore after stop', async () => {
    const pending = deferred<DeskImage | null>()
    vi.mocked(getDeskImage).mockReturnValueOnce(pending.promise)
    localStorage.setItem(
      deskPreferenceKey,
      JSON.stringify({ ...emptyDesk(), imageId: 'old-image', on: true }),
    )
    store.start()
    store.stop()
    pending.resolve(storedImage())
    await flushPromises()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(store.active).toBe(false)
  })

  it('ignores an obsolete restore after another tab removes the background', async () => {
    const pending = deferred<DeskImage | null>()
    vi.mocked(getDeskImage).mockReturnValueOnce(pending.promise)
    localStorage.setItem(
      deskPreferenceKey,
      JSON.stringify({ ...emptyDesk(), imageId: 'old-image', on: true }),
    )
    store.start()
    localStorage.removeItem(deskPreferenceKey)
    window.dispatchEvent(new StorageEvent('storage', { key: deskPreferenceKey }))
    pending.resolve(storedImage())
    await flushPromises()
    expect(store.preferences).toEqual(emptyDesk())
    expect(store.active).toBe(false)
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('falls back to plain paper if the saved image is missing', async () => {
    vi.mocked(getDeskImage).mockResolvedValueOnce(null)
    localStorage.setItem(
      deskPreferenceKey,
      JSON.stringify({ ...emptyDesk(), imageId: 'old-image', on: true }),
    )
    store.start()
    await flushPromises()
    expect(store.active).toBe(false)
    expect(store.preferences.imageId).toBeNull()
    expect(store.notice).toContain('已恢复纯纸面')
    expect(JSON.parse(localStorage.getItem(deskPreferenceKey)!)).toMatchObject({
      on: false,
      imageId: null,
    })
  })

  it('replaces the stored image and disposes the previous blob and URL', async () => {
    await restoreImage()
    await store.importImage(new File(['new'], 'new.png'), palette, 8)
    expect(putDeskImage).toHaveBeenCalledWith(storedImage('new-image'))
    expect(deleteDeskImage).toHaveBeenCalledWith('old-image')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:desk-test')
    expect(store.image?.id).toBe('new-image')
    expect(store.preferences.imageId).toBe('new-image')
    expect(store.active).toBe(true)
    expect(store.busy).toBe(false)
  })

  it('preserves the current image when decoding fails', async () => {
    await restoreImage()
    vi.mocked(prepareDeskImage).mockRejectedValueOnce(new Error('invalid image'))
    await store.importImage(new File(['bad'], 'bad.png'), palette, 8)
    expect(store.image?.id).toBe('old-image')
    expect(store.active).toBe(true)
    expect(store.notice).toBe('invalid image')
    expect(putDeskImage).not.toHaveBeenCalled()
    expect(deleteDeskImage).not.toHaveBeenCalled()
    expect(store.busy).toBe(false)
  })

  it('removes a newly stored candidate if saving preferences fails', async () => {
    await restoreImage()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    await store.importImage(new File(['new'], 'new.png'), palette, 8)
    expect(deleteDeskImage).toHaveBeenCalledTimes(1)
    expect(deleteDeskImage).toHaveBeenCalledWith('new-image')
    expect(store.image?.id).toBe('old-image')
    expect(store.active).toBe(true)
    expect(store.notice).toBe('无法保存背景设置。')
  })

  it('cleans up a pending import after stop rather than installing its URL', async () => {
    const pending = deferred<DeskImage>()
    vi.mocked(prepareDeskImage).mockReturnValueOnce(pending.promise)
    const importing = store.importImage(new File(['new'], 'new.png'), palette, 8)
    expect(store.busy).toBe(true)
    store.stop()
    pending.resolve(storedImage('new-image'))
    await importing
    expect(deleteDeskImage).toHaveBeenCalledTimes(1)
    expect(deleteDeskImage).toHaveBeenCalledWith('new-image')
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(store.active).toBe(false)
    expect(store.busy).toBe(false)
  })
})
