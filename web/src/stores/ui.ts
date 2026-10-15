import { computed, onScopeDispose, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useLayoutStore } from './layout'
import { desktopEffects } from '@/composables/useElectron'
import { i18n, resolveLanguage, t, type Language } from '@/i18n'

export type ThemeMode = 'system' | 'light' | 'dark'
export type Accent = 'stone' | 'blue' | 'green' | 'earth' | 'lotus' | 'ink'
export type TextSize = 'small' | 'default' | 'large'
export type EnterBehavior = 'send' | 'newline'
export interface Appearance {
  textSize?: TextSize
  enterBehavior?: EnterBehavior
  confirmEnterSend?: boolean
  language?: Language
  theme: ThemeMode
  accent: Accent
  texture: boolean
  greetingName: string
  avatar: string
  capsuleShortcut: string
  showContextUsage?: boolean
}
export const accents: Array<{ id: Accent; label: string }> = [
  { id: 'stone', label: '石青' },
  { id: 'blue', label: '黛蓝' },
  { id: 'green', label: '竹青' },
  { id: 'earth', label: '赭石' },
  { id: 'lotus', label: '藕荷' },
  { id: 'ink', label: '墨' },
]
export function validAppearance(value: unknown): value is Appearance {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return (
    ['system', 'light', 'dark'].includes(String(p.theme)) &&
    accents.some((a) => a.id === p.accent) &&
    typeof p.texture === 'boolean' &&
    (p.textSize === undefined || ['small', 'default', 'large'].includes(String(p.textSize))) &&
    (p.enterBehavior === undefined || p.enterBehavior === 'send' || p.enterBehavior === 'newline') &&
    (p.confirmEnterSend === undefined || typeof p.confirmEnterSend === 'boolean') &&
    (p.language === undefined || ['zh-CN', 'en', 'system'].includes(String(p.language))) &&
    (p.showContextUsage === undefined || typeof p.showContextUsage === 'boolean') &&
    typeof p.greetingName === 'string' &&
    p.greetingName.length <= 32 &&
    typeof p.avatar === 'string' &&
    Array.from(p.avatar).length === 1 &&
    typeof p.capsuleShortcut === 'string' &&
    p.capsuleShortcut.length <= 80
  )
}
export const useUIStore = defineStore('ui', () => {
  const fallback: Appearance = {
    language: 'zh-CN',
    theme: 'system',
    accent: 'stone',
    texture: true,
    greetingName: '你',
    avatar: '助',
    capsuleShortcut: 'Alt+Space',
    showContextUsage: true,
    textSize: 'default',
    enterBehavior: 'newline',
    confirmEnterSend: false,
  }
  const storageKey = import.meta.env.VITE_MOCK === '1' ? 'maigokite_inkline_demo' : 'maigokite_inkline'
  let stored: unknown
  try {
    stored = JSON.parse(localStorage.getItem(storageKey) || 'null')
  } catch {
    stored = null
  }
  const appearance = ref<Appearance>(validAppearance(stored) ? stored : fallback)
  const desktop = desktopEffects
  let desktopReady = !desktop
  const media = window.matchMedia?.('(prefers-color-scheme: dark)')
  const systemDark = ref(media?.matches ?? false)
  const theme = computed(() =>
    appearance.value.theme === 'system'
      ? systemDark.value
        ? 'dark'
        : 'light'
      : appearance.value.theme,
  )
  const layout = useLayoutStore()
  const drawerOpen = ref(false)
  const drawerTab = ref<'changes' | 'jobs' | 'plan' | 'files' | 'pending'>('changes')
  const filePreviewRequest = ref<{ path: string; folderId: string; serial: number } | null>(null)
  let filePreviewSerial = 0
  const chatTurnRequest = ref<{ sessionId: string; turnId: string; serial: number } | null>(null)
  let chatTurnSerial = 0
  const commandOpen = ref(false),
    shortcutsOpen = ref(false)
  function setAppearance(patch: Partial<Appearance>) {
    const next = { ...appearance.value, ...patch }
    if (validAppearance(next)) appearance.value = next
  }
  async function setCapsuleShortcut(value: string) {
    const next = { ...appearance.value, capsuleShortcut: value }
    if (!validAppearance(next)) throw new Error(t('快捷键格式不正确'))
    if (desktop) await desktop.setAppearance(next)
    appearance.value = next
  }
  function openDrawer(tab = drawerTab.value) {
    drawerTab.value = tab
    drawerOpen.value = true
  }
  // A presentation request, not file contents or a second workspace authority.
  function openFilePreview(path: string, folderId: string) {
    filePreviewRequest.value = { path, folderId, serial: ++filePreviewSerial }
    openDrawer('files')
  }
  // One-shot navigation intent only; no task or conversation state is copied here.
  function locateChatTurn(sessionId: string, turnId: string) {
    chatTurnRequest.value = { sessionId, turnId, serial: ++chatTurnSerial }
  }
  function clearChatTurnRequest(serial: number) {
    if (chatTurnRequest.value?.serial === serial) chatTurnRequest.value = null
  }
  function closeOverlays() {
    drawerOpen.value = commandOpen.value = shortcutsOpen.value = false
  }
  function apply() {
    const locale = resolveLanguage(appearance.value.language, navigator.language)
    i18n.global.locale.value = locale
    document.documentElement.lang = locale
    document.documentElement.dataset.theme = theme.value
    document.documentElement.dataset.accent = appearance.value.accent
    document.documentElement.dataset.texture = String(appearance.value.texture)
    document.documentElement.dataset.textSize = appearance.value.textSize || 'default'
    try {
      localStorage.setItem(storageKey, JSON.stringify(appearance.value))
    } catch {
      /* Storage may be unavailable. */
    }
  }
  const onSystem = (event: MediaQueryListEvent) => {
    systemDark.value = event.matches
  }
  media?.addEventListener('change', onSystem)
  const onLanguageChange = () => {
    if (appearance.value.language === 'system') apply()
  }
  window.addEventListener('languagechange', onLanguageChange)
  watch(
    [appearance, theme],
    () => {
      apply()
      if (desktopReady) void desktop?.setAppearance(appearance.value).catch(() => undefined)
    },
    { deep: true, immediate: true },
  )
  // Main owns cross-window appearance. Read it before publishing local defaults.
  if (desktop)
    void desktop
      .getAppearance()
      .then((value) => {
        if (validAppearance(value)) appearance.value = value
        desktopReady = true
        return desktop.setAppearance(appearance.value)
      })
      .catch(() => {
        desktopReady = true
      })
  const off = desktop?.onAppearance((value) => {
    if (validAppearance(value) && JSON.stringify(value) !== JSON.stringify(appearance.value))
      appearance.value = value
  })
  onScopeDispose(() => {
    media?.removeEventListener('change', onSystem)
    window.removeEventListener('languagechange', onLanguageChange)
    off?.()
  })
  return {
    appearance,
    theme,
    layout,
    drawerOpen,
    drawerTab,
    filePreviewRequest,
    chatTurnRequest,
    commandOpen,
    shortcutsOpen,
    setAppearance,
    setCapsuleShortcut,
    openDrawer,
    openFilePreview,
    locateChatTurn,
    clearChatTurnRequest,
    closeOverlays,
  }
})
