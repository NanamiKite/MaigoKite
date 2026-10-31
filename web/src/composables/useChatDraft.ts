import { onBeforeUnmount, watch, type Ref } from 'vue'
import { useChatViewStore, type ChatDraft } from '@/stores/chat-view'
import type { AttachmentReference } from '@/api/images'

export function useChatDraft(
  key: Ref<string>,
  text: Ref<string>,
  images: Ref<AttachmentReference[]>,
  pendingFiles: Ref<File[]>,
  mode: Ref<ChatDraft['mode']>,
  defaultMode: () => ChatDraft['mode'],
) {
  const view = useChatViewStore()
  function save(id: string) {
    view.drafts.set(id, {
      text: text.value,
      images: images.value.map((image) => ({ ...image })),
      pendingFiles: [...pendingFiles.value],
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
      pendingFiles.value = draft ? [...draft.pendingFiles] : []
      mode.value = draft?.mode || defaultMode()
    },
    { immediate: true, flush: 'sync' },
  )
  onBeforeUnmount(() => save(key.value))
  function clearSent(id: string, sent: ChatDraft) {
    // A response can arrive after switching tabs; compare the whole submitted draft.
    const draft = view.drafts.get(id)
    if (key.value === id || !draft || draft.pendingFiles.length) return
    if (
      draft.text !== sent.text ||
      draft.mode !== sent.mode ||
      draft.images.length !== sent.images.length
    )
      return
    if (
      draft.images.every((image, index) => {
        const original = sent.images[index]
        return (
          image.path === original.path &&
          image.folder_id === original.folder_id &&
          image.sha256 === original.sha256
        )
      })
    )
      view.drafts.delete(id)
  }
  return { save, clearSent }
}
