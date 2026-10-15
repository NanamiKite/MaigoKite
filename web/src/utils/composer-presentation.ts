import type { PermissionMode } from '@/api/client'
import type { ModelOption } from '@/config/model-groups'
import type { ImageReference } from '@/api/images'
export function menuSelectionIndex(key: string, current: number, count: number): number | null {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp'].includes(key)) return null
  if (!count) return -1
  if (key === 'Home') return 0
  if (key === 'End') return count - 1
  if (key === 'PageDown' || key === 'PageUp')
    return Math.max(0, Math.min(count - 1, current + (key === 'PageDown' ? 8 : -8)))
  return (current + (key === 'ArrowDown' ? 1 : -1) + count) % count
}
// Shared props for presentation fragments, not a new input/runtime abstraction.
export interface ComposerProps {
  sessionId: string
  assistantName?: string
  isSending?: boolean
  isSubmitting?: boolean
  isStopping?: boolean
  modelId?: string
  modelOptions?: ModelOption[]
  thinking?: string
  savingThinking?: boolean
  permissionMode?: PermissionMode
  lastMessage?: string
  waiting?: boolean
  queuedCount?: number
  guideReason?: string
  submit?: (
    content: string,
    intent: 'send' | 'guide' | 'later',
    images?: ImageReference[],
  ) => Promise<void>
}
export const composerDefaults = {
  assistantName: '助手',
  isSending: false,
  isSubmitting: false,
  isStopping: false,
  modelId: '',
  modelOptions: (): ModelOption[] => [],
  thinking: '',
  savingThinking: false,
  permissionMode: 'workspace' as const,
  lastMessage: '',
  queuedCount: 0,
}
export interface ComposerEmits {
  (event: 'send', content: string): void
  (event: 'cancel'): void
  (event: 'model-change', value: string): void
  (event: 'thinking-change', value: string): void
  (event: 'permission-mode-change', value: PermissionMode): void
}
