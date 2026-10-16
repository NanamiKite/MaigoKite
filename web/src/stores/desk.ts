import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { deskPreferences, emptyDesk, type DeskPreferences } from '@/utils/desk-preferences'
import { prepareDeskImage, softPixels, type DeskImage } from '@/utils/desk-image'
import {
  getDeskImage,
  putDeskImage,
  deleteDeskImage,
  deskPreferenceKey,
} from '@/utils/desk-storage'
import {
  maxImageWeight,
  plateAlpha,
  quantizedColors,
  type DeskPalette,
} from '@/utils/desk-contrast'
import { paletteKey } from '@/utils/desk-palette'

export const useDeskStore = defineStore('desk', () => {
  const preferences = ref(emptyDesk())
  const image = shallowRef<DeskImage | null>(null)
  const clearUrl = ref(''),
    softUrl = ref(''),
    busy = ref(false),
    notice = ref('')
  const maximum = ref<number | null>(null),
    chatMaximum = ref<number | null>(null),
    dim = ref(0),
    analysisMs = ref(0)
  const alpha = computed(() =>
    plateAlpha(preferences.value.visibility, maximum.value ?? 0, dim.value),
  )
  const chatAlpha = computed(() =>
    plateAlpha(preferences.value.visibility, chatMaximum.value ?? 0, dim.value),
  )
  const active = computed(() => preferences.value.on && !!image.value && maximum.value !== null)
  const cache = new Map<string, { shell: number; chat: number }>()
  let revision = 0,
    calculation = 0
  function persist(next: DeskPreferences) {
    const valid = deskPreferences(next)
    if (!valid) return false
    try {
      localStorage.setItem(deskPreferenceKey, JSON.stringify(valid))
    } catch {
      notice.value = '无法保存背景设置。'
      return false
    }
    preferences.value = valid
    return true
  }
  function release() {
    if (clearUrl.value) URL.revokeObjectURL(clearUrl.value)
    if (softUrl.value) URL.revokeObjectURL(softUrl.value)
    clearUrl.value = softUrl.value = ''
    image.value = null
    maximum.value = null
    chatMaximum.value = null
    calculation++
  }
  function install(value: DeskImage) {
    release()
    image.value = value
    clearUrl.value = URL.createObjectURL(value.clear)
    softUrl.value = URL.createObjectURL(value.soft)
    cache.clear()
  }
  async function restore() {
    const current = ++revision
    let stored: unknown
    try {
      stored = JSON.parse(localStorage.getItem(deskPreferenceKey) || 'null')
    } catch {
      stored = null
    }
    preferences.value = deskPreferences(stored) || emptyDesk()
    const id = preferences.value.imageId
    if (!id) {
      release()
      return
    }
    if (image.value?.id === id) return
    release()
    try {
      const value = await getDeskImage(id)
      if (current !== revision) return
      if (!value) throw new Error()
      install(value)
    } catch {
      if (current !== revision) return
      persist({ ...preferences.value, on: false, imageId: null })
      notice.value = '背景图片已失效，已恢复纯纸面。'
    }
  }
  async function importImage(file: File, palette: DeskPalette, blur: number) {
    if (busy.value) return
    busy.value = true
    notice.value = ''
    const current = ++revision,
      previous = preferences.value.imageId
    let candidate: DeskImage | undefined
    try {
      candidate = await prepareDeskImage(file, palette.paper, blur)
      await putDeskImage(candidate)
      if (
        current !== revision ||
        !persist({
          ...preferences.value,
          imageId: candidate.id,
          on: true,
          focal: { x: 0.5, y: 0.5 },
        })
      ) {
        await deleteDeskImage(candidate.id)
        return
      }
      install(candidate)
      if (previous) await deleteDeskImage(previous)
    } catch (error) {
      if (current === revision)
        notice.value = error instanceof Error ? error.message : '图片无法读取，请选择另一张。'
    } finally {
      busy.value = false
    }
  }
  async function remove() {
    if (busy.value) return
    busy.value = true
    revision++
    try {
      if (preferences.value.imageId) await deleteDeskImage(preferences.value.imageId)
      const saved = persist({ ...preferences.value, on: false, imageId: null })
      release()
      cache.clear()
      if (saved) notice.value = ''
    } catch {
      notice.value = '图片存储不可用。'
    } finally {
      busy.value = false
    }
  }
  function change(patch: Partial<Pick<DeskPreferences, 'on' | 'visibility' | 'focal'>>) {
    persist({ ...preferences.value, ...patch })
  }
  function imageFailed() {
    release()
    persist({ ...preferences.value, on: false, imageId: null })
    notice.value = '背景图片已失效，已恢复纯纸面。'
  }
  async function calculate(palette: DeskPalette, theme: string, chatPalette = palette) {
    maximum.value = null
    chatMaximum.value = null
    dim.value = palette.dim
    const current = ++calculation,
      value = image.value
    if (!value) return
    const key = `${theme}:${paletteKey(palette)}:${paletteKey(chatPalette)}:${value.id}`
    const cached = cache.get(key)
    if (cached) {
      maximum.value = cached.shell
      chatMaximum.value = cached.chat
      return
    }
    try {
      const pixels = await softPixels(value.soft)
      if (current !== calculation) return
      const started = performance.now()
      const colors = quantizedColors(pixels)
      const limit = maxImageWeight(colors, palette)
      const chatLimit = maxImageWeight(colors, chatPalette)
      analysisMs.value = performance.now() - started
      cache.set(key, { shell: limit, chat: chatLimit })
      maximum.value = limit
      chatMaximum.value = chatLimit
    } catch {
      if (current === calculation) imageFailed()
    }
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key === deskPreferenceKey || event.key === null) void restore()
  }
  function start() {
    window.addEventListener('storage', onStorage)
    void restore()
  }
  function stop() {
    revision++
    window.removeEventListener('storage', onStorage)
    release()
    cache.clear()
  }
  return {
    preferences,
    image,
    clearUrl,
    softUrl,
    busy,
    notice,
    alpha,
    chatAlpha,
    active,
    analysisMs,
    start,
    stop,
    change,
    importImage,
    remove,
    calculate,
    imageFailed,
  }
})
