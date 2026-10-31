import { t } from '@/i18n'
import type { components } from './generated/transport'

export type ErrorParameters = NonNullable<components['schemas']['ErrorDetail']['params']>

const messages: Record<string, { text: string; numbers?: string[] }> = {
  LOCAL_AUTH_REQUIRED: { text: '无法验证桌面连接，请退出并重新启动应用。' },
  OCR_UNAVAILABLE: { text: '请安装 Tesseract 和所需语言包，或切换到支持图片的模型。' },
  OCR_FAILED: { text: '文字识别失败，请检查图片和语言包。' },
  OCR_TIMEOUT: { text: '文字识别超时，请裁剪图片后重试。' },
  DOCUMENT_RENDER_FAILED: { text: '页面无法显示，请检查文档。' },
  DOCUMENT_QUERY_INVALID: { text: '请输入 1 至 200 个字符的搜索内容。' },
  DOCUMENT_ACTION_INVALID: { text: '目录和搜索仅支持文档模式。' },
  DOCUMENT_INVALID: { text: '文档已损坏，无法读取。' },
  DOCUMENT_ENCRYPTED: { text: '请先解密文档后再添加。' },
  DOCUMENT_TOO_COMPLEX: { text: '文档超过处理上限，请拆分后再添加。' },
  DOCUMENT_UNSUPPORTED: { text: '请选择 PDF、DOCX、XLSX 或 PPTX 文档。' },
  DOCUMENT_RANGE_INVALID: { text: '页码或段落编号超出文档范围。' },
  DOCUMENT_TIMEOUT: { text: '文档处理超时，请拆分后重试。' },
  DOCUMENT_PARSE_FAILED: { text: '文档处理失败，请检查文档或拆分后重试。' },
  ATTACHMENT_CHANGED: { text: '附件内容已改变，请重新添加后发送。' },
  ATTACHMENT_TOO_LARGE: { text: '文件不能超过 16 MiB。' },
  ATTACHMENT_SCOPE_INVALID: { text: '附件必须位于当前工作区内。' },
  ATTACHMENT_ACCESS_DENIED: { text: '此文件不能作为附件读取。' },
  ATTACHMENT_READER_REQUIRED: { text: '附件超出当前可用输入预算，请减少附件或启用文件读取工具。' },
  IMAGE_INPUT_UNSUPPORTED: { text: '当前模型不支持图片输入，请切换模型后发送。' },
  ATTACHMENT_ENCODING_INVALID: { text: '文件编码无效。' },
  ATTACHMENT_FORMAT_UNSUPPORTED: {
    text: '请选择 UTF-8 文本、PDF、DOCX、XLSX、PPTX，或 PNG、JPEG、WebP、GIF 图片。',
  },
  MODEL_CATALOG_CHANGED: { text: '模型设置已更改，请重新打开编辑页面。' },
  MODEL_BATCH_INVALID: { text: '模型编辑内容无效，请重新选择模型后保存。' },
  MODEL_LIMIT: { text: '最多配置 32 个模型，请先移除不再使用的模型。' },
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
  FILE_DOWNLOAD_TOO_LARGE: { text: '文件超过下载大小限制（48 MiB）。' },
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
