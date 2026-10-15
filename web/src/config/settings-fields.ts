import { t } from '@/i18n'
export interface SettingsField {
  id: string
  tab: string
  label: string
  keywords?: string
}
export const settingsFields: SettingsField[] = [
  { id: 'language', tab: 'general', label: '语言', keywords: 'language locale English' },
  { id: 'assistant-name', tab: 'general', label: '助手名字', keywords: 'name assistant' },
  { id: 'greeting-name', tab: 'general', label: '怎么称呼你', keywords: 'greeting name' },
  { id: 'avatar', tab: 'general', label: '头像字', keywords: 'avatar' },
  { id: 'theme', tab: 'general', label: '纸页', keywords: 'theme dark light' },
  { id: 'accent', tab: 'general', label: '强调色', keywords: 'accent color' },
  { id: 'text-size', tab: 'general', label: '字号', keywords: 'font text size' },
  { id: 'texture', tab: 'general', label: '纸纹', keywords: 'texture paper' },
  {
    id: 'enter-behavior',
    tab: 'general',
    label: '发送快捷键',
    keywords: 'alt s enter keyboard input send newline confirm 回车 发送消息 换行 二次确认',
  },
  {
    id: 'context-usage',
    tab: 'general',
    label: '显示模型上下文窗口用量',
    keywords: 'context tokens',
  },
  { id: 'capsule-shortcut', tab: 'general', label: '速记快捷键', keywords: 'capsule shortcut' },
  { id: 'models', tab: 'models', label: '模型', keywords: 'provider default model' },
  {
    id: 'subagent-calls',
    tab: 'subagents',
    label: '每次任务最多派发（0 关闭）',
    keywords: 'subagent default delegate 子代理 默认 派发上限',
  },
  {
    id: 'subagent-concurrent',
    tab: 'subagents',
    label: '最多同时运行',
    keywords: 'subagent concurrent 子代理 并行 并发',
  },
  {
    id: 'subagent-models',
    tab: 'subagents',
    label: '允许指定的其他模型',
    keywords: 'subagent models 子代理 模型',
  },
  {
    id: 'usage-statistics',
    tab: 'statistics',
    label: '用量统计',
    keywords: 'statistics usage tokens balance quota cache 统计 余额 额度 缓存 供应商',
  },
  {
    id: 'model-base-url',
    tab: 'models',
    label: '服务地址',
    keywords: 'base URL endpoint relay 中转站',
  },
  { id: 'model-api-key', tab: 'models', label: 'API Key', keywords: 'credential secret 密钥' },
  {
    id: 'model-selection',
    tab: 'models',
    label: '模型（可多选）',
    keywords: 'models catalog discover',
  },
  { id: 'tool-selection', tab: 'tools', label: '助手可用工具', keywords: 'tools' },
  { id: 'search-tools', tab: 'tools', label: '联网搜索', keywords: 'Tavily Google DuckDuckGo' },
  {
    id: 'image-generation',
    tab: 'tools',
    label: '图片生成',
    keywords: 'image generation 生图 多模态 OpenAI',
  },
  {
    id: 'permission-mode',
    tab: 'permissions',
    label: '执行权限',
    keywords: 'permission approval risk',
  },
  { id: 'safety-enabled', tab: 'safety', label: '提示词安全审查', keywords: 'safety review 安全' },
  {
    id: 'safety-base-url',
    tab: 'safety',
    label: '审查服务地址',
    keywords: 'decision base URL endpoint',
  },
  { id: 'safety-model', tab: 'safety', label: '决策模型', keywords: 'decision model 模型 ID' },
  {
    id: 'safety-api-key',
    tab: 'safety',
    label: '审查服务密钥',
    keywords: 'credential API key 凭证',
  },
  {
    id: 'safety-format',
    tab: 'safety',
    label: '审查请求格式',
    keywords: 'jev kev jev_like laya openai format',
  },
  {
    id: 'safety-layout',
    tab: 'safety',
    label: 'JEV 布局',
    keywords: 'jev kev layout 内嵌 inline message state 布局',
  },
  { id: 'safety-state', tab: 'safety', label: '审查状态文本', keywords: 'state 自定义状态' },
  { id: 'safety-threshold', tab: 'safety', label: '风险阈值', keywords: 'risk threshold' },
  {
    id: 'safety-trusted',
    tab: 'safety',
    label: '可信模型',
    keywords: 'trusted models whitelist 白名单',
  },
  {
    id: 'runtime-status',
    tab: 'workspace',
    label: '运行状态',
    keywords: 'backend core version 工作区 路径',
  },
]
export function searchSettings(query: string, fields = settingsFields) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return []
  return fields.filter((field) => {
    const text = `${field.label} ${t(field.label)} ${field.keywords || ''}`.toLocaleLowerCase()
    return words.every((word) => text.includes(word))
  })
}
