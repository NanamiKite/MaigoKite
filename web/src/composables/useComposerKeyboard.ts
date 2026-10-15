import { onScopeDispose, watch, type Ref } from 'vue'
import { useUIStore } from '@/stores/ui'
import { confirmAction } from '@/utils/confirm-action'
import { t } from '@/i18n'

interface ComposerKeyboardOptions {
  draft: Ref<string>
  context: () => readonly [string, string, string, boolean]
  lastMessage: () => string
  canSend: () => boolean
  isSending: () => boolean
  closeMenu: () => boolean
  send: (later?: boolean) => Promise<void>
  cancel: () => void
}

export function useComposerKeyboard(options: ComposerKeyboardOptions) {
  const ui = useUIStore()
  let controller: AbortController | undefined
  // A confirmation belongs to this draft and conversation, not a future send.
  watch([
    options.draft, options.context,
    () => ui.appearance.enterBehavior, () => ui.appearance.confirmEnterSend,
  ], () => controller?.abort(), { flush: 'sync' })
  onScopeDispose(() => controller?.abort())

  async function sendWithConfirmation() {
    if (controller || !options.canSend()) return
    if (!ui.appearance.confirmEnterSend) {
      await options.send()
      return
    }
    const request = new AbortController()
    controller = request
    try {
      const queuing = options.isSending()
      const accepted = await confirmAction(t(queuing ? '将这条消息加入队列？' : '发送这条消息？'), false, {
        title: t(queuing ? '确认加入队列' : '确认发送'),
        confirmLabel: t(queuing ? '加入队列' : '发送'), signal: request.signal,
      })
      if (accepted && !request.signal.aborted && options.canSend()) await options.send()
    } finally {
      if (controller === request) controller = undefined
    }
  }

  return function handleKeydown(event: KeyboardEvent) {
    if (event.isComposing || event.keyCode === 229 || event.defaultPrevented) return
    if (event.key === 'Escape' && options.closeMenu()) {
      event.preventDefault()
      event.stopPropagation()
      return
    }
    if (event.key === 'Escape' && !ui.drawerOpen && !ui.commandOpen && !ui.shortcutsOpen) {
      event.stopPropagation()
      options.cancel()
      return
    }
    if (event.key === 'ArrowUp' && !options.draft.value) {
      event.preventDefault()
      options.draft.value = options.lastMessage()
      return
    }
    const altSend = event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey &&
      (event.key.toLowerCase() === 's' || event.code === 'KeyS')
    if (altSend) {
      event.preventDefault()
      if (!event.repeat) void options.send()
      return
    }
    if (event.key !== 'Enter' || event.shiftKey) return
    // Keep the existing queue and Ctrl/Cmd+Enter shortcuts independent of plain Enter.
    if (event.altKey || event.ctrlKey || event.metaKey) {
      event.preventDefault()
      if (!event.repeat) void options.send(event.altKey ? true : undefined)
    } else if (ui.appearance.enterBehavior === 'send') {
      event.preventDefault()
      if (!event.repeat) void sendWithConfirmation()
    }
  }
}
