import type { AgentEvent } from './client'
import type { GatewayEvent, PlanItem, ToolHintAction, TurnSummary } from './events'
import { mapModelEvent } from './model-events'
import { mapSubagentEvent } from './subagent-events'
import { t } from '@/i18n'

const readTools = new Set([
  'file_read',
  'read',
  'glob',
  'grep',
  'find',
  'ls',
  'web_search',
  'web_fetch',
  'skill',
  'delegate',
  'list_subagent_models',
])
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}
function text(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2)
}
// Never navigate to arbitrary model/tool supplied URLs from an error hint.
function hintAction(value: unknown, tool: string): ToolHintAction | undefined {
  const destinations: Record<string, string> = {
    '/settings?tab=tools': '设置工具',
    '/settings?tab=tools&field=image-generation': '配置生图服务',
    '/settings?tab=models': '设置模型',
    '/settings?tab=permissions': '设置权限',
    '/settings?tab=workspace': '查看运行状态',
    '/integrations': '查看扩展',
  }
  const to = object(value).to
  if (typeof to === 'string' && Object.prototype.hasOwnProperty.call(destinations, to))
    return { to, label: t(destinations[to]) }
  if (tool === 'web_search') return { to: '/settings?tab=tools', label: t('设置联网工具') }
}

/** Keep the Gateway DTO intact; translate only at the UI boundary. */
export function mapGatewayEvent(event: AgentEvent): GatewayEvent[] {
  const p = event.payload,
    data = object(p.data)
  const base = {
    seq: event.sequence,
    eventId: event.event_id,
    sessionId: event.conversation_id,
    turnId: event.task_id || 'session',
    time: event.occurred_at,
  }
  const modelEvent = mapModelEvent(event, base)
  if (modelEvent) return [modelEvent]
  const childEvents = mapSubagentEvent(event)
  if (childEvents) return childEvents
  if (event.type === 'plan.updated' && event.task_id && Array.isArray(p.items)) {
    const items = p.items.flatMap((value): PlanItem[] => {
      const item = object(value)
      const status = item.status
      return typeof item.content === 'string' &&
        (status === 'pending' || status === 'in_progress' || status === 'completed')
        ? [{ content: item.content, status }]
        : []
    })
    // Invalid records are not an authoritative empty plan.
    return items.length === p.items.length ? [{ ...base, type: 'plan_update', items }] : []
  }
  if (
    ['task.input.queued', 'task.input.consumed'].includes(event.type) &&
    event.task_id &&
    typeof p.input_id === 'string' &&
    typeof p.content === 'string'
  )
    return [
      {
        ...base,
        type: 'input_update',
        input: {
          id: p.input_id,
          task_id: event.task_id,
          content: p.content,
          status: event.type === 'task.input.consumed' ? 'consumed' : 'queued',
          created_at: event.occurred_at || '',
        },
      },
    ]
  const tool = String(p.tool_name || t('工具'))
  const stepId = String(p.tool_call_id || event.event_id)
  if (event.type === 'task.resumed') return [{ ...base, type: 'turn_resumed' }]
  if (event.type === 'task.waiting_approval') return [{ ...base, type: 'turn_waiting' }]
  if (event.type === 'message.delta')
    return [{ ...base, type: 'text_delta', text: text(p.content) }]
  if (event.type === 'reasoning.started') return [{ ...base, type: 'thinking_started' }]
  if (event.type === 'reasoning.delta')
    return [{ ...base, type: 'thinking_delta', text: text(p.content || p.summary) }]
  if (event.type === 'reasoning.completed')
    return [{ ...base, type: 'thinking_completed', text: text(p.summary || p.content) }]
  if (['tool.proposed', 'tool.requested', 'tool.started'].includes(event.type)) {
    const args = object(p.arguments)
    const browserRead =
      tool === 'browser' && ['snapshot', 'screenshot'].includes(String(args.action))
    const title =
      tool === 'delegate'
        ? t('委派只读子 Agent')
        : tool === 'file_read'
          ? t('读取 {0}', [String(args.path || t('文件'))])
          : ['bash', 'exec_command', 'shell'].includes(tool)
            ? String(args.command || t('执行命令'))
            : String(p.title || tool)
    return [
      {
        ...base,
        type: 'step_start',
        stepId,
        tool,
        kind: readTools.has(tool) || browserRead ? 'read' : 'act',
        title,
        input: p.arguments || {},
        pending: event.type !== 'tool.started',
      },
    ]
  }
  if (['tool.completed', 'tool.failed'].includes(event.type)) {
    const exitCode =
      typeof data.return_code === 'number'
        ? data.return_code
        : typeof data.exit_code === 'number'
          ? data.exit_code
          : typeof p.exit_code === 'number'
            ? p.exit_code
            : undefined
    const ok =
      event.type === 'tool.completed' &&
      data.success !== false &&
      !data.error &&
      (exitCode === undefined || exitCode === 0)
    const output = [data.stdout, data.stderr]
      .filter((value) => typeof value === 'string' && value)
      .join('\n')
    return [
      {
        ...base,
        type: 'step_end',
        stepId,
        ok: Boolean(ok),
        output: output || text(data.content || p.data || p.summary || p.message),
        exitCode,
        durationMs: Number(
          p.duration_ms || data.duration_ms || Number(data.duration_seconds || 0) * 1000,
        ),
        hint:
          typeof p.user_hint === 'string'
            ? p.user_hint
            : !ok && tool === 'web_search'
              ? t('联网搜索暂不可用，请检查联网工具的连接与凭证。')
              : undefined,
        errorCode: !ok && typeof p.error_code === 'string' ? p.error_code : undefined,
        action: !ok ? hintAction(p.action, tool) : undefined,
      },
    ]
  }
  if (event.type === 'approval.requested')
    return [
      {
        ...base,
        type: 'await',
        awaitId: String(p.approval_id || p.id),
        mode: 'approval',
        title: t('批准 {0}', [tool]),
        detail: text(p.arguments || p.resource_scope),
        risk: String(p.risk_level || ''),
        version: Number(p.version || 1),
        toolCallId: typeof p.tool_call_id === 'string' ? p.tool_call_id : undefined,
      },
    ]
  if (event.type === 'approval.decided')
    return [
      {
        ...base,
        type: 'await_resolved',
        awaitId: String(p.approval_id || ''),
        verdict: p.decision === 'allow' ? 'allow' : 'deny',
        note: typeof p.note === 'string' ? p.note : undefined,
      },
    ]
  if (event.type === 'question.requested')
    return [
      {
        ...base,
        type: 'await',
        awaitId: String(p.question_id || ''),
        mode: 'question',
        title: text(p.title),
        detail: '',
        version: Number(p.version || 1),
        toolCallId: typeof p.tool_call_id === 'string' ? p.tool_call_id : undefined,
        options: Array.isArray(p.options)
          ? p.options.flatMap((option) => {
              const value = object(option)
              return typeof value.label === 'string'
                ? [{ label: value.label, recommended: value.recommended === true }]
                : []
            })
          : [],
      },
    ]
  if (event.type === 'question.answered')
    return [
      {
        ...base,
        type: 'question_answered',
        awaitId: String(p.question_id || ''),
        answer: text(p.answer),
      },
    ]
  if (
    [
      'task.succeeded',
      'task.completed',
      'task.failed',
      'task.cancelled',
      'task.interrupted',
      'task.timed_out',
    ].includes(event.type)
  ) {
    const failed = ['task.failed', 'task.interrupted', 'task.timed_out'].includes(event.type)
    return [
      ...(failed
        ? [{ ...base, type: 'error' as const, message: text(p.message || t('执行失败')) }]
        : []),
      {
        ...base,
        type: 'turn_end',
        status: event.type === 'task.cancelled' ? 'cancelled' : failed ? 'failed' : 'completed',
        summary:
          p.summary && typeof p.summary === 'object'
            ? (p.summary as unknown as TurnSummary)
            : undefined,
      },
    ]
  }
  if (event.type === 'error')
    return [{ ...base, type: 'error', message: text(p.message || p.content || t('执行失败')) }]
  if (event.type === 'memory.added')
    return [
      { ...base, type: 'memory_added', memoryId: String(p.memory_id || ''), text: text(p.content) },
    ]
  if (event.type === 'file.changed')
    return [{ ...base, type: 'file_change', path: String(p.path || ''), diff: text(p.diff) }]
  return []
}
