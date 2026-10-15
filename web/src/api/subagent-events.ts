import type { AgentEvent } from './client'
import type { GatewayEvent, Step } from './events'
import { t } from '@/i18n'

export type SubagentStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export interface SubagentMessage {
  id: string
  content: string
  reasoning: string
  final: boolean
  truncated: boolean
  time?: string
}
export interface SubagentRun {
  id: string
  task: string
  label: string
  model: string
  provider: string
  status: SubagentStatus
  stateSeq: number
  summary: string
  messages: SubagentMessage[]
  steps: Step[]
  timeline: { kind: 'message' | 'step'; id: string; seq: number }[]
}
export type SubagentUpdate =
  | {
      kind: 'state'
      status: SubagentStatus
      task?: string
      label?: string
      model?: string
      provider?: string
      summary?: string
    }
  | { kind: 'message'; message: SubagentMessage }
  | {
      kind: 'tool'
      id: string
      name: string
      input: unknown
      output?: string
      ok?: boolean
      durationMs?: number
      truncated?: boolean
    }

const string = (value: unknown) => (typeof value === 'string' ? value : '')

/** Child evidence stays on the parent task journal, never in its reply text. */
export function mapSubagentEvent(event: AgentEvent): GatewayEvent[] | null {
  if (!event.type.startsWith('subagent.')) return null
  const p = event.payload
  const childId = string(p.subagent_id)
  if (!event.task_id || !childId) return []
  const base = {
    seq: event.sequence,
    eventId: event.event_id,
    sessionId: event.conversation_id,
    turnId: event.task_id,
    time: event.occurred_at,
  }
  let update: SubagentUpdate
  if (event.type === 'subagent.message') {
    if (!string(p.message_id)) return []
    update = {
      kind: 'message',
      message: {
        id: string(p.message_id),
        content: string(p.content),
        reasoning: string(p.reasoning),
        final: p.final === true,
        truncated: p.truncated === true,
        time: event.occurred_at,
      },
    }
  } else if (event.type === 'subagent.progress' || event.type === 'subagent.tool.completed') {
    update = {
      kind: 'tool',
      id: string(p.tool_call_id),
      name: string(p.tool_name),
      input: p.arguments || {},
      ...(event.type === 'subagent.tool.completed'
        ? {
            output: string(p.output),
            ok: p.ok === true,
            durationMs: typeof p.duration_ms === 'number' ? Math.max(0, p.duration_ms) : 0,
            truncated: p.truncated === true,
          }
        : {}),
    }
  } else {
    const statuses: Record<string, SubagentStatus> = {
      'subagent.queued': 'queued',
      'subagent.started': 'running',
      'subagent.completed': 'completed',
      'subagent.failed': 'failed',
      'subagent.cancelled': 'cancelled',
    }
    const status = statuses[event.type]
    if (!status) return []
    update = {
      kind: 'state',
      status,
      task: typeof p.task === 'string' ? p.task : undefined,
      label: typeof p.label === 'string' ? p.label : undefined,
      model: string(p.name || p.model) || undefined,
      provider: string(p.provider) || undefined,
      summary:
        typeof p.summary === 'string'
          ? p.summary
          : typeof p.message === 'string'
            ? p.message
            : undefined,
    }
  }
  const mapped: GatewayEvent[] = [{ ...base, type: 'subagent_update', childId, update }]
  if (['subagent.queued', 'subagent.started', 'subagent.progress'].includes(event.type)) {
    const pending = event.type === 'subagent.queued'
    const model = [string(p.name || p.model), string(p.provider)].filter(Boolean).join(' · ')
    const label = string(p.label) || t('子 Agent')
    mapped.push({
      ...base,
      type: 'step_start',
      stepId: childId,
      tool: 'delegate',
      kind: 'read',
      pending,
      title: pending
        ? t('{0} 等待中 · 已派发 {1}/{2}', [label, p.used, p.limit])
        : `${label}${model ? ` · ${model}` : ''} · ${event.type === 'subagent.progress' ? string(p.tool_name) : t('执行中')}`,
      // Progress must not overwrite the original delegated task with read arguments.
      input:
        event.type === 'subagent.progress'
          ? undefined
          : {
              task: p.task,
              label: p.label,
              model_id: p.model_id,
              model: p.model,
              provider: p.provider,
            },
    })
  } else if (
    update.kind === 'state' &&
    ['completed', 'failed', 'cancelled'].includes(update.status)
  ) {
    mapped.push({
      ...base,
      type: 'step_end',
      stepId: childId,
      ok: update.status === 'completed',
      output: update.summary || t('子 Agent 已停止'),
      durationMs: 0,
    })
  }
  return mapped
}
