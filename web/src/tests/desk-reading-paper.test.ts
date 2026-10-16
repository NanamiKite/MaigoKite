import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useDeskStore } from '@/stores/desk'
import { softPixels } from '@/utils/desk-image'
import { maxImageWeight, plateAlpha, type DeskPalette } from '@/utils/desk-contrast'

vi.mock('@/utils/desk-image', () => ({ softPixels: vi.fn() }))
const shell: DeskPalette = {
  paper: [248, 245, 239],
  dim: 0.25,
  pairs: [{ surface: [240, 236, 227], ink: [98, 101, 92], target: 4.5 }],
}
const chat: DeskPalette = {
  ...shell,
  pairs: [{ surface: shell.paper, ink: [77, 83, 75], target: 4.5 }],
}
beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(softPixels)
    .mockReset()
    .mockResolvedValue(new Uint8ClampedArray([4, 4, 4, 255]))
})
function storeWithImage() {
  const store = useDeskStore()
  store.image = { id: 'trial', width: 1, height: 1, clear: new Blob(), soft: new Blob() }
  return store
}
describe('independent reading-paper limits', () => {
  it('keeps the shell limit while giving the single chat sheet its own safe weight', async () => {
    const store = storeWithImage()
    await store.calculate(shell, 'light', chat)
    const visibility = store.preferences.visibility
    expect(store.alpha).toBe(plateAlpha(visibility, maxImageWeight([[4, 4, 4]], shell), shell.dim))
    expect(store.chatAlpha).toBe(
      plateAlpha(visibility, maxImageWeight([[4, 4, 4]], chat), chat.dim),
    )
    expect(store.chatAlpha).toBeLessThan(store.alpha)
    store.preferences.visibility = 0
    expect(store.alpha).toBe(1)
    expect(store.chatAlpha).toBe(1)
  })
  it('keys both palettes and never reuses a chat limit for different text ink', async () => {
    const store = storeWithImage()
    await store.calculate(shell, 'light', chat)
    await store.calculate(shell, 'light', chat)
    expect(softPixels).toHaveBeenCalledTimes(1)
    await store.calculate(shell, 'light', shell)
    expect(softPixels).toHaveBeenCalledTimes(2)
    expect(store.chatAlpha).toBe(store.alpha)
  })
  it('covers both planes during a theme change and ignores obsolete decoding', async () => {
    const store = storeWithImage()
    await store.calculate(shell, 'light', chat)
    let finish: ((value: Uint8ClampedArray) => void) | undefined
    vi.mocked(softPixels).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const pending = store.calculate(shell, 'dark', chat)
    expect(store.alpha).toBe(1)
    expect(store.chatAlpha).toBe(1)
    await store.calculate(shell, 'light', shell)
    finish?.(new Uint8ClampedArray([252, 252, 252, 255]))
    await pending
    expect(store.chatAlpha).toBe(store.alpha)
  })
})
