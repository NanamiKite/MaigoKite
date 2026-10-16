import { onBeforeUnmount, watch, type Ref } from 'vue'
import { useChatViewStore, type ChatDraft } from '@/stores/chat-view'
import type { ImageReference } from '@/api/images'

export function useChatDraft(
  key: Ref<string>,
  text: Ref<string>,
  images: Ref<ImageReference[]>,
  mode: Ref<ChatDraft['mode']>,
  defaultMode: () => ChatDraft['mode'],
) {
  const view = useChatViewStore()
  function save(id: string) {
    view.drafts.set(id, {
      text: text.value,
      images: images.value.map((image) => ({ ...image })),
      mode: mode.value,
    })
  }
  watch(
    key,
    (next, previous) => {
      if (previous !== undefined) save(previous)
      const draft = view.drafts.get(next)
      text.value = draft?.text || ''
      images.value = draft?.images.map((image) => ({ ...image })) || []
      mode.value = draft?.mode || defaultMode()
    },
    { immediate: true, flush: 'sync' },
  )
  onBeforeUnmount(() => save(key.value))
  function clearSent(id: string, sentText: string) {
    // A send can complete after switching tabs. Never clear a newer draft.
    if (key.value !== id && view.drafts.get(id)?.text === sentText) view.drafts.delete(id)
  }
  return { save, clearSent }
}
