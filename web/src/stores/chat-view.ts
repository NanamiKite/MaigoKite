import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AttachmentReference } from '@/api/images'

export interface ChatDraft {
  text: string
  images: AttachmentReference[]
  pendingFiles: File[]
  mode: 'ask' | 'plan' | 'auto'
}
export interface ChatPosition {
  top: number
  following: boolean
  count: number
}

// Presentation only. Closing a tab never touches sessions, subscriptions or tasks.
// Drafts stay in this window's memory, not localStorage or the model context.
export const useChatViewStore = defineStore('chat-view', () => {
  const opened = ref<string[]>([])
  const drafts = new Map<string, ChatDraft>()
  const positions = new Map<string, ChatPosition>()
  const panels = new Map<string, 'changes' | 'jobs' | 'plan' | 'files' | 'pending'>()
  function open(id: string) {
    if (id && !opened.value.includes(id)) opened.value.push(id)
  }
  function close(id: string) {
    opened.value = opened.value.filter((item) => item !== id)
  }
  function forget(id: string) {
    close(id)
    drafts.delete(id)
    positions.delete(id)
    panels.delete(id)
  }
  return { opened, drafts, positions, panels, open, close, forget }
})
