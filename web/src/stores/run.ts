import { computed, onScopeDispose, shallowRef, triggerRef } from 'vue'
import { defineStore } from 'pinia'
import type { AwaitItem, ContextUsage, GatewayEvent, PulseState, Turn } from '@/api/events'
import type { AgentTask, ConversationSnapshot, TaskInput, TaskInspection } from '@/api/client'
import { summarize } from './run-projection'
import { projectSubagentEvent } from './run-subagents'
import { latestContext, projectModelEvent, sessionUsage, type UsageStats } from './run-usage'
export type { PulseState } from '@/api/events'

export const useRunStore = defineStore('run', () => {
  const turns = shallowRef<Record<string, Turn[]>>({})
  const metrics = shallowRef<Record<string, { usage: UsageStats; context?: ContextUsage }>>({})
  const dirtyMetrics = new Set<string>()
  const connected = shallowRef<Record<string, boolean>>({})
  const seen = new Set<string>()
  const reasoningStreams = new Set<string>()
  const stateSequences = new Map<string, number>()
  const dirtyTurns = new Map<string, Set<string>>()
  const dirtyCollections = new Set<string>()
  let frame = 0
  function publish() {
    if (!frame)
      frame = requestAnimationFrame(() => {
        frame = 0
        // Token deltas must not rescan the whole transcript for statistics.
        if (dirtyMetrics.size) {
          const next = { ...metrics.value }
          for (const id of dirtyMetrics) {
            const list = turns.value[id] || []
            next[id] = { usage: sessionUsage(list), context: latestContext(list) }
          }
          dirtyMetrics.clear()
          metrics.value = next
        }
        // Text-only frames retain activity identities so Rail does not rebuild
        // unchanged tools, approvals and inputs for every token.
        const nextTurns = { ...turns.value }
        for (const [id, ids] of dirtyTurns) {
          if (!nextTurns[id]) continue
          nextTurns[id] = nextTurns[id].map((turn) => {
            if (!ids.has(turn.id)) return turn
            if (!dirtyCollections.has(`${id}:${turn.id}`)) return { ...turn }
            return {
              ...turn,
              steps: turn.steps.map((step) => ({ ...step })),
              awaits: turn.awaits.map((item) => ({ ...item })),
              timeline: [...turn.timeline],
              memories: [...turn.memories],
              changes: turn.changes.map((item) => ({ ...item })),
              plan: [...turn.plan],
              inputs: turn.inputs?.map((item) => ({ ...item })),
              jobs: turn.jobs.map((item) => ({ ...item })),
              modelCalls: turn.modelCalls ? { ...turn.modelCalls } : undefined,
            }
          })
        }
        dirtyTurns.clear()
        dirtyCollections.clear()
        turns.value = nextTurns
      })
  }
  function getTurn(
    sessionId: string,
    turnId: string,
    time?: string,
    collectionsChanged = true,
  ): Turn {
    const dirty = dirtyTurns.get(sessionId) || new Set<string>()
    dirty.add(turnId)
    dirtyTurns.set(sessionId, dirty)
    if (collectionsChanged) dirtyCollections.add(`${sessionId}:${turnId}`)
    const list = (turns.value[sessionId] ||= [])
    let turn = list.find((t) => t.id === turnId)
    if (!turn) {
      turn = {
        id: turnId,
        sessionId,
        status: 'running',
        startedAt: time ? Date.parse(time) : Date.now(),
        text: '',
        thinking: '',
        steps: [],
        awaits: [],
        memories: [],
        timeline: [],
        changes: [],
        plan: [],
        inputs: [],
        jobs: [],
      }
      list.push(turn)
      list.sort((a, b) => a.startedAt - b.startedAt)
    }
    return turn
  }
  function markStarted(
    sessionId: string,
    turnId: string,
    userText?: string,
    time?: string,
    attachmentsJSON?: string,
  ) {
    const turn = getTurn(sessionId, turnId, time)
    turn.userText ||= userText
    if (attachmentsJSON) turn.attachmentsJSON = attachmentsJSON
    publish()
  }
  function hydrateTask(sessionId: string, task: AgentTask, pendingIds?: Set<string>, sequence = 0) {
    const key = `${sessionId}:${task.id}`
    if (sequence < (stateSequences.get(key) || 0)) return
    stateSequences.set(key, sequence)
    const turn = getTurn(sessionId, task.id, task.created_at)
    turn.startedAt = Date.parse(task.created_at)
    turns.value[sessionId].sort((a, b) => a.startedAt - b.startedAt)
    turn.userText ||= task.input_text
    turn.status =
      task.status === 'succeeded'
        ? 'completed'
        : task.status === 'cancelled'
          ? 'cancelled'
          : ['failed', 'interrupted', 'timed_out'].includes(task.status)
            ? 'failed'
            : task.status === 'waiting_approval'
              ? 'waiting'
              : 'running'
    turn.error = task.last_error || undefined
    const terminal = ['completed', 'failed', 'cancelled'].includes(turn.status)
    if (terminal && turn.thinkingStatus === 'running') turn.thinkingStatus = 'completed'
    if (!terminal) {
      turn.endedAt = undefined
      turn.summary = undefined
    } else {
      turn.endedAt ||= Date.parse(task.updated_at)
      turn.summary = { ...summarize(turn), contextPct: turn.summary?.contextPct ?? null }
    }
    for (const item of turn.awaits)
      if (!item.verdict && item.answer === undefined)
        item.closed = terminal || (pendingIds ? !pendingIds.has(item.id) : Boolean(item.closed))
    publish()
  }
  function hydratePending(sessionId: string, snapshot: ConversationSnapshot) {
    // Pending product facts have the snapshot's real watermark, not a fabricated
    // runtime event identity. Event replay remains responsible for activity history.
    const pending: AwaitItem[] = [
      ...snapshot.approvals.map((approval): AwaitItem => ({
        id: approval.id,
        sessionId,
        turnId: approval.task_id,
        mode: 'approval',
        title: `批准 ${approval.tool_name}`,
        detail: JSON.stringify(approval.arguments, null, 2),
        risk: approval.risk_level,
        version: approval.version,
        toolCallId: approval.tool_call_id,
      })),
      ...snapshot.questions.map((question): AwaitItem => ({
        id: question.id,
        sessionId,
        turnId: question.task_id,
        mode: 'question',
        title: question.title,
        detail: '',
        options: question.options,
        version: question.version,
        toolCallId: question.tool_call_id,
        time: question.created_at,
      })),
    ]
    for (const item of pending) {
      const key = `${sessionId}:${item.turnId}`
      if (snapshot.last_sequence < (stateSequences.get(key) || 0)) continue
      const turn = getTurn(sessionId, item.turnId)
      if (['completed', 'failed', 'cancelled'].includes(turn.status)) continue
      const existing = turn.awaits.find((a) => a.id === item.id)
      if (existing?.verdict || existing?.answer !== undefined) continue
      if (existing)
        Object.assign(existing, item, { closed: false, time: existing.time || item.time })
      else {
        turn.awaits.push({ ...item, closed: false })
        turn.timeline.push({ id: item.id, kind: 'await', time: item.time })
      }
      turn.status = 'waiting'
      stateSequences.set(key, snapshot.last_sequence)
    }
    publish()
  }
  function completeText(sessionId: string, turnId: string, text: string, messageId?: string) {
    const turn = getTurn(sessionId, turnId)
    turn.text = text
    turn.messageId = messageId
    publish()
  }
  function mergeInput(sessionId: string, input: TaskInput, seq = 0, readAfter = Infinity) {
    const turn = getTurn(sessionId, input.task_id)
    const list = (turn.inputs ||= [])
    const existing = list.find((item) => item.id === input.id)
    // A GET has no event watermark. Never overwrite a receipt newer than its request.
    if (
      existing &&
      ((existing.seq || 0) > readAfter ||
        (seq && seq < (existing.seq || 0)) ||
        (existing.status === 'consumed' && input.status !== 'consumed') ||
        (existing.status === 'not_consumed' &&
          input.status === 'queued' &&
          turn.status !== 'running'))
    )
      return
    if (existing)
      Object.assign(existing, input, {
        seq: seq || existing.seq,
        created_at: seq
          ? existing.created_at || input.created_at
          : input.created_at || existing.created_at,
      })
    else list.push({ ...input, seq: seq || undefined })
    publish()
  }
  function inputReadSequence(sessionId: string, taskId: string) {
    return Math.max(
      0,
      ...(turns.value[sessionId]?.find((turn) => turn.id === taskId)?.inputs || []).map(
        (item) => item.seq || 0,
      ),
    )
  }
  function hydrateInputs(
    sessionId: string,
    taskId: string,
    inputs: TaskInput[],
    readAfter: number,
  ) {
    for (const input of inputs)
      if (input.task_id === taskId) mergeInput(sessionId, input, 0, readAfter)
    publish()
  }
  function hydratePlan(sessionId: string, inspection: TaskInspection) {
    const turn = getTurn(sessionId, inspection.task.id)
    if (inspection.last_sequence < (turn.planSeq || 0)) return
    const items = inspection.projection.plan
    if (items !== null) {
      turn.plan = items
      turn.planSeq = inspection.last_sequence
    } else if (!inspection.projection.truncated) {
      turn.plan = []
      turn.planSeq = inspection.last_sequence
    }
    publish()
  }
  function setDetailStatus(
    sessionId: string,
    taskId: string,
    kind: 'plan' | 'inputs',
    loading: boolean,
    error = '',
  ) {
    const turn = getTurn(sessionId, taskId)
    if (kind === 'plan') {
      turn.planLoading = loading
      turn.planError = error
    } else {
      turn.inputsLoading = loading
      turn.inputsError = error
    }
    publish()
  }
  function markInputSent(sessionId: string, taskId: string, inputId: string, sentAsTaskId: string) {
    const turn = getTurn(sessionId, taskId)
    const item = turn.inputs?.find((input) => input.id === inputId)
    if (item) item.sentAsTaskId = sentAsTaskId
    publish()
  }
  function forgetSession(id: string) {
    delete turns.value[id]
    delete connected.value[id]
    const next = { ...metrics.value }
    delete next[id]
    metrics.value = next
    dirtyMetrics.delete(id)
    dirtyTurns.delete(id)
    for (const key of dirtyCollections) if (key.startsWith(`${id}:`)) dirtyCollections.delete(key)
    for (const key of stateSequences.keys())
      if (key.startsWith(`${id}:`)) stateSequences.delete(key)
    triggerRef(connected)
    publish()
  }
  function apply(event: GatewayEvent) {
    const itemId =
      'stepId' in event
        ? event.stepId
        : 'awaitId' in event
          ? event.awaitId
          : 'memoryId' in event
            ? event.memoryId
            : ''
    const identity = event.eventId
      ? `${event.eventId}:${event.type}`
      : `${event.sessionId}:${event.turnId}:${event.seq}:${event.type}:${itemId}`
    if (seen.has(identity)) return
    const existed = turns.value[event.sessionId]?.some((item) => item.id === event.turnId)
    const textOnly = [
      'text_delta',
      'thinking_delta',
      'thinking_started',
      'thinking_completed',
    ].includes(event.type)
    const previous = getTurn(event.sessionId, event.turnId, event.time, !textOnly)
    // Stage only the affected turn. Token deltas copy scalar fields; structural
    // events copy mutable collections so a failed reducer can be retried safely.
    const turn: Turn = textOnly
      ? { ...previous }
      : {
          ...previous,
          subagents: previous.subagents ? [...previous.subagents] : undefined,
          steps: previous.steps.map((item) => ({ ...item })),
          awaits: previous.awaits.map((item) => ({ ...item })),
          timeline: previous.timeline.map((item) => ({ ...item })),
          memories: [...previous.memories],
          changes: previous.changes.map((item) => ({ ...item })),
          plan: [...previous.plan],
          inputs: previous.inputs?.map((item) => ({ ...item })),
          jobs: previous.jobs.map((item) => ({ ...item })),
          modelCalls: previous.modelCalls ? { ...previous.modelCalls } : undefined,
        }
    const list = turns.value[event.sessionId]
    const index = list.indexOf(previous)
    list[index] = turn
    const stateKey = `${event.sessionId}:${event.turnId}`
    const staleState = event.seq <= (stateSequences.get(stateKey) || 0)
    try {
      switch (event.type) {
        case 'subagent_update':
          projectSubagentEvent(turn, event)
          break
        case 'model_start':
        case 'model_end':
        case 'model_failed':
        case 'context_update':
        case 'context_compaction':
          projectModelEvent(turn, event)
          dirtyMetrics.add(event.sessionId)
          break
        case 'turn_resumed':
          if (staleState) break
          turn.status = 'running'
          turn.error = undefined
          turn.endedAt = undefined
          turn.summary = undefined
          break
        case 'turn_waiting':
          // The status receipt precedes the versioned approval payload. Show the
          // wait immediately, but never invent an approval or its authorization.
          if (!staleState && !['completed', 'failed', 'cancelled'].includes(turn.status))
            turn.status = 'waiting'
          break
        case 'text_delta':
          turn.text += event.text
          break
        case 'thinking_started':
          if (!['completed', 'failed', 'cancelled'].includes(turn.status))
            turn.thinkingStatus = 'running'
          break
        case 'thinking_delta':
          turn.thinking += event.text
          turn.thinkingStatus = ['completed', 'failed', 'cancelled'].includes(turn.status)
            ? 'completed'
            : 'running'
          break
        case 'thinking_completed':
          if (!reasoningStreams.has(`${event.sessionId}:${event.turnId}`))
            turn.thinking += (turn.thinking ? '\n\n' : '') + event.text
          turn.thinkingStatus = 'completed'
          break
        case 'step_start': {
          // Replay can precede a snapshot-installed await. Retain real ordering
          // metadata without changing execution state or the snapshot watermark.
          const ordered = turn.timeline.find(
            (item) => item.id === event.stepId && item.kind === 'step',
          )
          if (ordered && event.seq > 0 && (!ordered.seq || event.seq < ordered.seq))
            Object.assign(ordered, { seq: event.seq, time: event.time })
          let step = turn.steps.find((s) => s.id === event.stepId)
          if (step) {
            // Tool progress updates the same activity, not the main reply.
            // Do not restart its clock on every read/progress receipt.
            if (event.title) step.title = event.title
            if (step.status === 'pending' && !event.pending) step.status = 'running'
            if (!event.pending && !step.startedAt)
              step.startedAt = event.time ? Date.parse(event.time) : Date.now()
            if (event.input) step.input = event.input
          } else {
            step = {
              id: event.stepId,
              tool: event.tool,
              kind: event.kind,
              title: event.title,
              input: event.input,
              status: event.pending ? 'pending' : 'running',
              output: '',
              durationMs: 0,
              startedAt: event.pending
                ? undefined
                : event.time
                  ? Date.parse(event.time)
                  : Date.now(),
            }
            turn.steps.push(step)
            turn.timeline.push({ id: step.id, kind: 'step', seq: event.seq, time: event.time })
          }
          break
        }
        case 'step_end': {
          dirtyMetrics.add(event.sessionId)
          let step = turn.steps.find((s) => s.id === event.stepId)
          if (!step) {
            step = {
              id: event.stepId,
              tool: '工具',
              kind: 'act',
              title: '工具结果',
              input: {},
              status: 'running',
              output: '',
              durationMs: 0,
            }
            turn.steps.push(step)
            turn.timeline.push({ id: step.id, kind: 'step', seq: event.seq, time: event.time })
          }
          if (step)
            Object.assign(step, {
              status: event.ok ? 'completed' : 'failed',
              output: event.output,
              durationMs:
                event.durationMs ||
                (step.startedAt
                  ? Math.max(0, (event.time ? Date.parse(event.time) : Date.now()) - step.startedAt)
                  : 0),
              exitCode: event.exitCode,
              hint: event.hint,
              errorCode: event.errorCode,
              action: event.action,
            })
          break
        }
        case 'await': {
          const existing = turn.awaits.find((a) => a.id === event.awaitId)
          const ordered = turn.timeline.find(
            (item) => item.id === event.awaitId && item.kind === 'await',
          )
          if (ordered && event.seq > 0 && (!ordered.seq || event.seq < ordered.seq))
            Object.assign(ordered, { seq: event.seq, time: event.time })
          if (existing) {
            existing.toolCallId ||= event.toolCallId
            if (event.seq > 0 && (!existing.seq || event.seq < existing.seq)) {
              existing.seq = event.seq
              existing.time = event.time || existing.time
            }
          }
          if (!existing) {
            turn.awaits.push({
              id: event.awaitId,
              sessionId: event.sessionId,
              turnId: event.turnId,
              mode: event.mode,
              title: event.title,
              detail: event.detail,
              risk: event.risk,
              options: event.options,
              version: event.version || 1,
              toolCallId: event.toolCallId,
              seq: event.seq,
              time: event.time,
              closed: staleState || ['completed', 'failed', 'cancelled'].includes(turn.status),
            })
            turn.timeline.push({
              id: event.awaitId,
              kind: 'await',
              seq: event.seq,
              time: event.time,
            })
          } else if (!staleState && !existing.verdict && existing.answer === undefined) {
            Object.assign(existing, {
              title: event.title,
              detail: event.detail,
              risk: event.risk,
              options: event.options,
              version: event.version || existing.version,
              closed: ['completed', 'failed', 'cancelled'].includes(turn.status),
            })
          }
          if (!staleState && !['completed', 'failed', 'cancelled'].includes(turn.status))
            turn.status = 'waiting'
          break
        }
        case 'await_resolved': {
          const item = turn.awaits.find((a) => a.id === event.awaitId)
          if (item) {
            item.verdict = event.verdict
            item.note = event.note
          }
          if (
            !staleState &&
            turn.status === 'waiting' &&
            turn.awaits.every((a) => a.verdict || a.closed)
          )
            turn.status = 'running'
          break
        }
        case 'question_answered': {
          const item = turn.awaits.find((a) => a.id === event.awaitId)
          if (item) {
            item.answer = event.answer
            item.closed = true
          }
          if (
            !staleState &&
            turn.status === 'waiting' &&
            turn.awaits.every((a) => a.verdict || a.closed)
          )
            turn.status = 'running'
          break
        }
        case 'file_change': {
          const item = turn.changes.find((c) => c.path === event.path)
          if (item) item.diff = event.diff
          else turn.changes.push({ path: event.path, diff: event.diff })
          break
        }
        case 'plan_update':
          if (event.seq > (turn.planSeq || 0)) {
            turn.plan = event.items
            turn.planSeq = event.seq
            turn.planError = ''
          }
          break
        case 'input_update':
          mergeInput(event.sessionId, event.input, event.seq)
          break
        case 'memory_added':
          if (!turn.memories.some((m) => m.id === event.memoryId)) {
            turn.memories.push({ id: event.memoryId, text: event.text })
            turn.timeline.push({
              id: event.memoryId,
              kind: 'memory',
              seq: event.seq,
              time: event.time,
            })
          }
          break
        case 'job_update': {
          const job = turn.jobs.find((j) => j.id === event.jobId)
          if (job) Object.assign(job, event)
          else turn.jobs.push({ id: event.jobId, status: event.status, logTail: event.logTail })
          break
        }
        case 'error':
          turn.timeline.push({
            id: `error-${event.seq}`,
            kind: 'error',
            seq: event.seq,
            time: event.time,
          })
          if (!staleState) {
            turn.error = event.message
            turn.status = 'failed'
            if (turn.thinkingStatus === 'running') turn.thinkingStatus = 'completed'
          }
          break
        case 'turn_end':
          // An error and its terminal projection share one event sequence.
          if (event.seq < (stateSequences.get(stateKey) || 0)) break
          turn.status = event.status || 'completed'
          turn.endedAt = event.time ? Date.parse(event.time) : Date.now()
          turn.summary = event.summary || summarize(turn)
          if (turn.thinkingStatus === 'running') turn.thinkingStatus = 'completed'
          for (const item of turn.awaits) if (!item.verdict) item.closed = true
          break
      }
      // Streaming text is published once per frame, never once per token.
      publish()
    } catch (error) {
      if (existed) list[index] = previous
      else list.splice(index, 1)
      throw error
    }
    if (event.type === 'thinking_delta') reasoningStreams.add(stateKey)
    if (
      !staleState &&
      [
        'turn_resumed',
        'turn_waiting',
        'await',
        'await_resolved',
        'question_answered',
        'error',
        'turn_end',
      ].includes(event.type)
    )
      stateSequences.set(stateKey, event.seq)
    seen.add(identity)
  }
  function removeMemory(id: string) {
    for (const list of Object.values(turns.value))
      for (const turn of list) {
        if (!turn.memories.some((item) => item.id === id)) continue
        getTurn(turn.sessionId, turn.id)
        turn.memories = turn.memories.filter((m) => m.id !== id)
        turn.timeline = turn.timeline.filter((n) => n.kind !== 'memory' || n.id !== id)
      }
    publish()
  }
  function setConnection(id: string, value: boolean) {
    connected.value = { ...connected.value, [id]: value }
  }
  function pulse(id: string | null): PulseState {
    if (id && connected.value[id] === false) return 'offline'
    const turn = id ? turns.value[id]?.slice(-1)[0] : undefined
    if (!turn) return 'idle'
    if (turn.status === 'waiting') return 'waiting'
    if (turn.status === 'failed') return 'error'
    if (['completed', 'cancelled'].includes(turn.status)) return 'idle'
    return turn.steps.some((s) => s.status === 'running') ? 'acting' : 'thinking'
  }
  const awaits = computed<AwaitItem[]>((previous) => {
    const next = Object.values(turns.value)
      .flatMap((list) => list.flatMap((t) => t.awaits))
      .filter((a) => !a.verdict && !a.closed)
    // Streaming must not invalidate every sidebar and pending-count selector.
    return previous &&
      previous.length === next.length &&
      next.every((item, index) => item === previous[index])
      ? previous
      : next
  })
  onScopeDispose(() => {
    if (frame) cancelAnimationFrame(frame)
  })
  return {
    forgetSession,
    turns,
    metrics,
    connected,
    awaits,
    getTurn,
    markStarted,
    hydrateTask,
    hydratePending,
    completeText,
    mergeInput,
    hydrateInputs,
    inputReadSequence,
    hydratePlan,
    setDetailStatus,
    markInputSent,
    apply,
    removeMemory,
    setConnection,
    pulse,
  }
})
