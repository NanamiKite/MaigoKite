import type { GatewayEvent, Turn } from '@/api/events'
import type { SubagentRun } from '@/api/subagent-events'
import { t } from '@/i18n'

export function projectSubagentEvent(
  turn: Turn,
  event: Extract<GatewayEvent, { type: 'subagent_update' }>,
) {
  const children = (turn.subagents ||= [])
  const index = children.findIndex((child) => child.id === event.childId)
  const previous = children[index]
  const call = turn.steps.find((step) => step.id === event.childId && step.tool === 'delegate')
  const args =
    call?.input && typeof call.input === 'object' ? (call.input as Record<string, unknown>) : {}
  // Copy only this child's presentation state. Other tabs retain their identities.
  const child: SubagentRun = previous
    ? {
        ...previous,
        messages: [...previous.messages],
        steps: previous.steps.map((step) => ({ ...step })),
        timeline: previous.timeline.map((item) => ({ ...item })),
      }
    : {
        id: event.childId,
        task: typeof args.task === 'string' ? args.task : '',
        label: typeof args.label === 'string' ? args.label : '',
        model: '',
        provider: '',
        status: 'queued',
        stateSeq: 0,
        summary: '',
        messages: [],
        steps: [],
        timeline: [],
      }
  if (index < 0) children.push(child)
  else children[index] = child
  const update = event.update
  if (update.kind === 'state') {
    // Replay can fill missing metadata, but cannot revive a completed child.
    for (const key of ['task', 'label', 'model', 'provider'] as const) {
      const value = update[key]
      if (value !== undefined && (!child[key] || event.seq >= child.stateSeq)) child[key] = value
    }
    if (event.seq >= child.stateSeq) {
      child.status = update.status
      child.stateSeq = event.seq
      if (update.summary !== undefined) child.summary = update.summary
    }
    return
  }
  if (update.kind === 'message') {
    const existing = child.messages.findIndex((item) => item.id === update.message.id)
    if (existing < 0) {
      child.messages.push(update.message)
      child.timeline.push({ kind: 'message', id: update.message.id, seq: event.seq })
    } else child.messages[existing] = update.message
  } else if (update.id) {
    let step = child.steps.find((item) => item.id === update.id)
    if (!step) {
      const input =
        update.input && typeof update.input === 'object'
          ? (update.input as Record<string, unknown>)
          : {}
      step = {
        id: update.id,
        tool: update.name,
        kind: 'read',
        title: String(input.path || input.pattern || update.name),
        input: update.input,
        output: '',
        status: 'running',
        durationMs: 0,
        startedAt: event.time ? Date.parse(event.time) : undefined,
      }
      child.steps.push(step)
      child.timeline.push({ kind: 'step', id: step.id, seq: event.seq })
    }
    const ordered = child.timeline.find((item) => item.kind === 'step' && item.id === update.id)
    if (ordered) ordered.seq = Math.min(ordered.seq, event.seq)
    if (update.output !== undefined) {
      step.output = update.output
      step.status = update.ok ? 'completed' : 'failed'
      step.durationMs = update.durationMs || 0
      step.hint = update.truncated ? t('输出较长，仅显示已保存的部分。') : undefined
    }
  }
  child.timeline.sort((a, b) => a.seq - b.seq)
}
