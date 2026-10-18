import { t } from '@/i18n'
import type { components } from './generated/transport'

export type ErrorParameters = NonNullable<components['schemas']['ErrorDetail']['params']>

const messages: Record<string, { text: string; numbers?: string[] }> = {
  INVALID_PERMISSION_MODE: { text: '无效的权限模式。' },
  INVALID_RESPONSE_MODE: { text: '无效的响应模式。' },
  INVALID_SOURCE_MESSAGE: { text: '来源消息标识无效。' },
  CONVERSATION_TITLE_LENGTH: {
    text: '对话名称须为 {min}–{max} 个字符。',
    numbers: ['min', 'max'],
  },
  CONVERSATION_BUSY: { text: '请先停止或完成此对话中的任务，再移动、归档或删除。' },
  WORKSPACE_FOLDER_CHANGED: { text: '工作区文件夹不存在或路径已变更，请重新选择文件夹。' },
  WORKSPACE_FOLDER_INVALID: { text: '请选择有效的文件夹。' },
  WORKSPACE_FOLDER_ABSOLUTE: { text: '工作区文件夹须使用绝对路径。' },
  WORKSPACE_NOT_DIRECTORY: { text: '工作区路径不是文件夹。' },
  WORKSPACE_NAME_LENGTH: { text: '工作区名称不能超过 {max} 个字符。', numbers: ['max'] },
  WORKSPACE_OWNER_REQUIRED: { text: '只有工作区所有者可以删除工作区。' },
  DEFAULT_WORKSPACE_PROTECTED: { text: '默认工作区不能删除。' },
  WORKSPACE_PROCESS_ACTIVE: { text: '工作区中有运行中的进程，请停止后再删除。' },
  WORKSPACE_TASK_ACTIVE: { text: '工作区中有未结束的任务，请完成或停止后再删除。' },
  WORKSPACE_TASK_RECOVERABLE: { text: '工作区中有可恢复的任务，请完成或停止后再删除。' },
  QUESTION_CHOICE_REQUIRED: { text: '请选择一个确认选项。' },
}

export function errorParameters(value: unknown): ErrorParameters {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const params: ErrorParameters = {}
  for (const [key, item] of Object.entries(value)) {
    if (
      typeof item === 'string' ||
      typeof item === 'boolean' ||
      (typeof item === 'number' && Number.isFinite(item))
    )
      params[key] = item
  }
  return params
}

export function errorCopy(code: string, fallback: string, params: ErrorParameters): string {
  const entry = Object.hasOwn(messages, code) ? messages[code] : undefined
  // Older servers and newer unknown codes retain the server's safe fallback.
  // Incomplete parameters must not expose an untranslated placeholder.
  if (!entry || entry.numbers?.some((key) => typeof params[key] !== 'number')) return fallback
  return t(entry.text, params)
}
