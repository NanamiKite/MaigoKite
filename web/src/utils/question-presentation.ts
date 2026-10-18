import { t } from '@/i18n'
import type { AwaitItem, QuestionOption } from '@/api/events'

export function questionOptionLabel(option: QuestionOption): string {
  if (option.value === 'safety.continue') return t('继续执行')
  if (option.value === 'safety.stop') return t('终止任务')
  return option.label
}

export function questionCaption(item: AwaitItem): string {
  if (item.toolCallId?.startsWith('safety_')) return t('提示词安全审查需要确认')
  return item.title?.trim() ? item.title : t('Agent 在等你回答')
}

export function questionAnswerLabel(item: AwaitItem): string {
  const option = item.options?.find((entry) => (entry.value || entry.label) === item.answer)
  return option ? questionOptionLabel(option) : item.answer || ''
}
