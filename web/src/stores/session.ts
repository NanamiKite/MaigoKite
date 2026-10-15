import { t } from '@/i18n'
import { computed, onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  api,
  type AgentEvent,
  type AgentTask,
  type Approval,
  type ChatMessage,
  type Conversation,
  type MessageReceipt,
  type PermissionMode,
  type TaskInput,
} from '@/api/client'
import { GatewayError } from '@/api/http'
import { EventSocket } from '@/api/socket'
import { mapGatewayEvent } from '@/api/event-adapter'
import { useRunStore } from './run'
import { activityGroups, projectMessage } from './session-projection'
import { useWorkspaceStore } from './workspace'
import { useSettingsStore } from './settings'
import { copySubagentSettings, type SubagentSettings } from '@/config/subagents'

export const useSessionStore = defineStore('session', () => {
  const workspace = useWorkspaceStore()
  const showArchived = ref(false)
  const sessions = ref<Conversation[]>([]),
    messages = ref<Record<string, ChatMessage[]>>({})
  const activeSessionId = ref<string | null>(null),
    isLoading = ref(false),
    error = ref<string | null>(null)
  const runningTaskIds = ref<Record<string, string[]>>({}),
    approvals = ref<Record<string, Approval[]>>({})
  const queuedMessages = ref<Record<string, { id: string; content: string }[]>>({})
  const queuedPaused = ref<Record<string, boolean>>({})
  const stoppingTaskIds = ref<string[]>([])
  const tasks = ref<Record<string, AgentTask[]>>({})
  const capabilities = ref<Record<string, boolean>>({})
  const historyTruncated = ref<Record<string, boolean>>({})
  const resumingTaskIds = ref<string[]>([])
  // One key per logical submission, retained after an ambiguous transport error.
  // This is client request bookkeeping, not execution state or an automatic retry queue.
  const submissions = ref<
    Record<
      string,
      {
        key: string
        content: string
        uncertain: boolean
        inputOrigin?: { taskId: string; inputId: string }
      }
    >
  >({})
  const inputSubmissions = ref<
    Record<string, { key: string; content: string; uncertain: boolean }>
  >({})
  const inputErrors = ref<Record<string, string>>({})
  const detailRequests = new Map<string, { controller: AbortController; promise: Promise<void> }>()
  const resendingInputs = new Set<string>()
  const snapshotFacts = new Map<
    string,
    {
      sequence: number
      tasks: Map<string, AgentTask>
      pendingIds?: Set<string>
    }
  >()
  const taskSequences = new Map<string, number>()
  const isStopping = computed(() =>
    (runningTaskIds.value[activeSessionId.value || ''] || []).some((id) =>
      stoppingTaskIds.value.includes(id),
    ),
  )
  const queuedAfterTask = new Map<string, string>(),
    draining = new Set<string>()
  const loadingSessions = new Map<string, Promise<void>>()
  const deletedSessions = new Set<string>()
  const installingSnapshots = new Set<string>()
  const run = useRunStore(),
    streams = new Map<string, EventSocket>()
  const activeSession = computed(() =>
    sessions.value.find((item) => item.id === activeSessionId.value),
  )
  const visibleSessions = computed(() =>
    sessions.value.filter(
      (item) =>
        (item.folder_id || '') === workspace.selectedId &&
        Boolean(item.archived) === showArchived.value,
    ),
  )
  const activeMessages = computed(() =>
    activeSessionId.value ? messages.value[activeSessionId.value] || [] : [],
  )
  const activeApprovals = computed(() =>
    activeSessionId.value ? approvals.value[activeSessionId.value] || [] : [],
  )
  const isSending = computed(() =>
    Boolean(activeSessionId.value && runningTaskIds.value[activeSessionId.value]?.length),
  )
  const activeActivityGroups = computed(() =>
    activityGroups(run.turns[activeSessionId.value || ''] || []),
  )
  const activeActivities = computed(() =>
    activeActivityGroups.value.flatMap((group) => group.items),
  )
  const activities = computed(() =>
    Object.fromEntries(
      Object.entries(run.turns).map(([id, turns]) => [
        id,
        activityGroups(turns).flatMap((g) => g.items),
      ]),
    ),
  )
  const taskActivityStatuses = computed(() =>
    Object.fromEntries(
      Object.entries(run.turns).map(([id, turns]) => [
        id,
        Object.fromEntries(activityGroups(turns).map((g) => [g.task_id, g.status])),
      ]),
    ),
  )
  let generation = 0,
    lifecycle = 0
  function mark(id: string, task: string) {
    const list = (runningTaskIds.value[id] ||= [])
    if (task && !list.includes(task)) list.push(task)
  }
  function clear(id: string, task: string) {
    runningTaskIds.value[id] = (runningTaskIds.value[id] || []).filter((item) => item !== task)
  }
  function handleEvent(id: string, event: AgentEvent, replay = false) {
    if (deletedSessions.has(id)) return
    // The socket owns buffering. Returning false leaves both identity and replay
    // cursor unacknowledged until the snapshot is installed and projection runs.
    if (installingSnapshots.has(id)) return false
    const task = event.task_id || ''
    const snapshot = snapshotFacts.get(id)
    const covered = Boolean(snapshot && event.sequence <= snapshot.sequence)
    const stateKey = `${id}:${task}`
    const stateEvent =
      !event.type.startsWith('task.input.') &&
      ['task.', 'approval.', 'question.'].some((prefix) => event.type.startsWith(prefix))
    const staleState = stateEvent && event.sequence <= (taskSequences.get(stateKey) || 0)
    // Project the fallible runtime mapping first. Its reducer commits each
    // identity atomically, so a retry cannot duplicate a successful text delta.
    for (const mapped of mapGatewayEvent(event)) run.apply(mapped)
    if (!covered && !staleState && ['task.started', 'task.resumed'].includes(event.type)) {
      const known = tasks.value[id]?.find((item) => item.id === task)
      if (known) known.status = 'running'
    }
    if (event.type === 'task.started') {
      if (!covered && !staleState) mark(id, task)
      run.markStarted(
        id,
        task,
        String(event.payload.input_text || '') || undefined,
        event.occurred_at,
      )
    }
    if (!covered && !staleState && event.type === 'task.resumed') mark(id, task)
    if (!covered && !staleState && event.type === 'task.waiting_approval') {
      mark(id, task)
      const known = tasks.value[id]?.find((item) => item.id === task)
      if (known) known.status = 'waiting_approval'
    }
    // Live text belongs to run.turns. Persisted messages update only on completion;
    // otherwise every delta bypasses the run store's frame batching via this ref.
    if (!covered && event.type === 'message.completed')
      projectMessage((messages.value[id] ||= []), event, replay)
    if (event.type === 'message.completed' && typeof event.payload.content === 'string')
      run.completeText(id, task, event.payload.content, String(event.payload.message_id || ''))
    // Rebuild historical activity, but never let replay regress atomic product facts.
    if (covered) {
      const fact = snapshot?.tasks.get(task)
      if (fact && event.type !== 'message.delta' && event.type !== 'reasoning.delta')
        run.hydrateTask(id, fact, snapshot?.pendingIds, snapshot?.sequence)
      return
    }
    if (staleState) return
    if (event.type === 'approval.requested') {
      const pending = (approvals.value[id] ||= [])
      const approval = {
        ...event.payload,
        id: String(event.payload.approval_id || event.payload.id || ''),
        conversation_id: id,
        task_id: task,
      } as unknown as Approval
      if (approval.id && !pending.some((a) => a.id === approval.id)) pending.push(approval)
      mark(id, task)
    }
    if (event.type === 'approval.decided')
      approvals.value[id] = (approvals.value[id] || []).filter(
        (a) => a.id !== String(event.payload.approval_id),
      )
    if (['question.requested', 'question.answered'].includes(event.type)) mark(id, task)
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
      clear(id, task)
      approvals.value[id] = (approvals.value[id] || []).filter((a) => a.task_id !== task)
      if (event.type === 'task.failed') error.value = String(event.payload.message || t('执行失败'))
      const succeeded = ['task.succeeded', 'task.completed'].includes(event.type)
      if (!succeeded && queuedAfterTask.get(id) === task) queuedPaused.value[id] = true
      if (!replay && succeeded) void sendNext(id, task)
      const epoch = lifecycle
      if (!replay)
        void (async () => {
          await loadingSessions.get(id)
          if (epoch === lifecycle && !deletedSessions.has(id))
            await refreshTaskDetails(id, task, true)
          if (epoch === lifecycle && !deletedSessions.has(id)) await refreshSnapshot(id)
        })().catch(() => {
          error.value = t('任务状态更新失败，请重新打开对话。')
        })
    }
    if (stateEvent) taskSequences.set(stateKey, event.sequence)
  }
  function watchSession(id: string) {
    if (streams.has(id) || deletedSessions.has(id)) return
    let connectedOnce = false
    const syncError = t('对话状态同步失败，正在重新连接。当前执行状态尚未确认，请勿重复发送。')
    const stream = new EventSocket({
      connect: (receive) => api.connectEvents(id, receive),
      history: (after, signal) => api.listTaskEvents(id, after, signal),
      receive: (event, replay) => handleEvent(id, event, replay),
      failure: () => {
        error.value = syncError
      },
      connection: (online) => {
        const reconnected = online && connectedOnce && run.connected[id] === false
        // A task can commit its terminal row before its event is published.
        // Successful heartbeat replay also reconciles active product facts;
        // an open socket or an empty event page is not proof it is still running.
        const needsReconciliation = online &&
          (runningTaskIds.value[id] || []).some((task) => !task.startsWith('pending-'))
        if (online) connectedOnce = true
        if (online && error.value === syncError) error.value = null
        run.setConnection(id, online)
        if (!online && queuedMessages.value[id]?.length) queuedPaused.value[id] = true
        // Snapshot installation calls retry() itself. Reuse that in-flight
        // snapshot rather than recursively starting another reconciliation.
        if ((reconnected || needsReconciliation) && !loadingSessions.has(id))
          void refreshSnapshot(id).catch(() => {
            error.value = t('连接已恢复，但任务状态尚未确认，请刷新对话，勿重复发送。')
          })
      },
    })
    streams.set(id, stream)
    stream.start()
  }
  function retainedWorkspace(conversation: Conversation): Conversation {
    return conversation.folder_id && workspace.wasRemoved(conversation.folder_id)
      ? { ...conversation, folder_id: '' } : conversation
  }
  function moveWorkspaceSessionsToDefault(folderId: string) {
    sessions.value = sessions.value.map(item => item.folder_id === folderId ? { ...item, folder_id: '' } : item)
  }
  async function loadSessions() {
    if (isLoading.value) return
    isLoading.value = true
    error.value = null
    try {
      await workspace.load()
      sessions.value = (await api.listConversations()).map(retainedWorkspace).filter(
        (item) => !deletedSessions.has(item.id),
      )
      const current = activeSession.value
      if (current) {
        workspace.select(current.folder_id || '')
        showArchived.value = Boolean(current.archived)
      }
    } catch (reason) {
      error.value =
        t('无法连接应用服务：') + (reason instanceof Error ? reason.message : String(reason))
    } finally {
      isLoading.value = false
    }
  }
  async function createSession(title: string, folderId = workspace.selectedId, subagents?: SubagentSettings) {
    try {
      const defaults = copySubagentSettings(subagents || useSettingsStore().subagentDefaults)
      if (defaults.calls || defaults.modelIds.length) {
        if (capabilities.value.subagents === undefined) capabilities.value = await api.getCapabilities()
        if (!capabilities.value.subagents)
          throw new Error(t('当前服务不支持子 Agent，请在设置中关闭默认配置后重试。'))
        if (defaults.modelIds.length && !capabilities.value.subagent_model_selection)
          throw new Error(t('当前服务不支持指定子 Agent 模型，请取消模型选择后重试。'))
      }
      // Every creation entry point uses the same defaults, in the create request itself.
      const item = retainedWorkspace(await api.createConversation({
        title,
        folder_id: folderId,
        max_subagent_calls: defaults.calls,
        max_concurrent_subagents: defaults.concurrent,
        subagent_model_ids: defaults.modelIds,
      }))
      showArchived.value = false
      sessions.value.unshift(item)
      await switchSession(item.id)
      return item
    } catch (reason) {
      error.value = String(reason)
      throw reason
    }
  }
  async function forkSession(id: string, key: string, messageId = '') {
    const item = retainedWorkspace(await api.forkConversation(id, key, messageId))
    if (!sessions.value.some((entry) => entry.id === item.id)) sessions.value.unshift(item)
    showArchived.value = false
    return item
  }
  async function switchSession(id: string, force = false) {
    if (!force && activeSessionId.value === id && messages.value[id] && streams.has(id)) return
    const current = ++generation
    activeSessionId.value = id
    error.value = null
    let conversation: Conversation
    try {
      conversation = retainedWorkspace(await api.getConversation(id))
    } catch (reason) {
      if (current === generation) {
        activeSessionId.value = null
        error.value =
          t('无法载入对话：') + (reason instanceof Error ? reason.message : String(reason))
      }
      return
    }
    if (current !== generation || deletedSessions.has(id)) return
    const index = sessions.value.findIndex((item) => item.id === id)
    if (index < 0) sessions.value.unshift(conversation)
    else sessions.value[index] = conversation
    workspace.select(conversation.folder_id || '')
    showArchived.value = Boolean(conversation.archived)
    try {
      await refreshSnapshot(id)
    } catch (reason) {
      if (current === generation)
        error.value =
          t('无法载入对话：') + (reason instanceof Error ? reason.message : String(reason))
    }
  }
  function refreshSnapshot(id: string): Promise<void> {
    if (deletedSessions.has(id)) return Promise.resolve()
    let loading = loadingSessions.get(id)
    if (!loading) {
      const epoch = lifecycle
      // Buffer before subscribing/fetching. Snapshot installation must never
      // overwrite an event already consumed by EventSocket's dedupe set.
      installingSnapshots.add(id)
      watchSession(id)
      loading = (async () => {
        try {
          const [snapshot, available] = await Promise.all([
            api.getConversationSnapshot(id),
            api.getCapabilities().catch(() => capabilities.value),
          ])
          if (epoch !== lifecycle || deletedSessions.has(id)) return
          // Text still streaming is projected in run.turns from event history.
          // Do not carry stale stream/local messages over persisted messages.
          messages.value[id] = snapshot.messages
          approvals.value[id] = snapshot.approvals
          if (snapshot.truncated) {
            const known = new Map((tasks.value[id] || []).map((task) => [task.id, task]))
            for (const task of snapshot.tasks) known.set(task.id, task)
            tasks.value[id] = [...known.values()].sort(
              (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
            )
          } else tasks.value[id] = snapshot.tasks
          capabilities.value = available
          historyTruncated.value[id] = snapshot.truncated
          const pendingIds = snapshot.truncated
            ? undefined
            : new Set([
                ...snapshot.approvals.map((item) => item.id),
                ...snapshot.questions.map((item) => item.id),
              ])
          snapshotFacts.set(id, {
            sequence: snapshot.last_sequence,
            tasks: new Map(snapshot.tasks.map((task) => [task.id, task])),
            pendingIds,
          })
          const includedTasks = new Set(snapshot.tasks.map((task) => task.id))
          const sending = (runningTaskIds.value[id] || []).filter(
            (task) =>
              task.startsWith('pending-') || (snapshot.truncated && !includedTasks.has(task)),
          )
          runningTaskIds.value[id] = [
            ...sending,
            ...snapshot.tasks
              .filter((t) =>
                ['created', 'queued', 'running', 'waiting_approval', 'retry_wait'].includes(
                  t.status,
                ),
              )
              .map((t) => t.id),
          ]
          for (const task of snapshot.tasks)
            run.hydrateTask(id, task, pendingIds, snapshot.last_sequence)
          run.hydratePending(id, snapshot)
          for (const task of snapshot.tasks)
            taskSequences.set(`${id}:${task.id}`, snapshot.last_sequence)
        } finally {
          if (epoch === lifecycle) installingSnapshots.delete(id)
          if (epoch === lifecycle) await streams.get(id)?.retry()
        }
        if (epoch === lifecycle && !deletedSessions.has(id)) {
          const latest = tasks.value[id]?.slice(-1)[0]
          // Older inputs are reconciled when the user expands transcript history.
          const known = (run.turns[id] || [])
            .slice(-20)
            .filter((turn) => turn.inputs?.some((input) => input.status !== 'consumed'))
          const ids = new Set([...(latest ? [latest.id] : []), ...known.map((turn) => turn.id)])
          await Promise.all([...ids].map((taskId) => refreshTaskDetails(id, taskId)))
        }
      })().finally(() => {
        if (loadingSessions.get(id) === loading) loadingSessions.delete(id)
      })
      loadingSessions.set(id, loading)
    }
    return loading
  }
  function requestKey() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
      b.toString(16).padStart(2, '0'),
    ).join('')
  }
  function steeringTask(id: string) {
    const ids = (runningTaskIds.value[id] || []).filter((task) => !task.startsWith('pending-'))
    return ids.length === 1 ? ids[0] : undefined
  }
  function steeringReason(id: string) {
    if (sessions.value.find((item) => item.id === id)?.archived) return t('此对话已归档。')
    if (!capabilities.value.task_input) return t('当前服务不支持引导任务。')
    const taskId = steeringTask(id)
    if (!taskId) return t('当前没有可引导的任务。')
    if (run.connected[id] === false) return t('连接恢复后可引导任务。')
    const turn = run.turns[id]?.find((item) => item.id === taskId)
    const task = tasks.value[id]?.find((item) => item.id === taskId)
    if (turn?.status === 'waiting' || (!turn && task?.status === 'waiting_approval'))
      return t('请先处理审批或回答问题；也可以在驳回时附上理由。')
    if (task && ['created', 'queued', 'retry_wait'].includes(task.status))
      return t('任务运行后可引导；现在可以稍后发送。')
    if (turn ? turn.status !== 'running' : task?.status !== 'running')
      return t('任务运行后可引导；现在可以稍后发送。')
    if ((turn?.userText || task?.input_text || '').startsWith('/tool '))
      return t('直接工具任务不接受引导，可以稍后发送。')
    if (stoppingTaskIds.value.includes(taskId)) return t('正在停止任务，可以稍后发送。')
    if (inputSubmissions.value[taskId]) return t('请先确认上一条补充的提交结果。')
    if ((turn?.inputs || []).filter((item) => item.status === 'queued').length >= 20)
      return t('已有 20 条补充等待纳入，请等待处理后再提交。')
    return ''
  }
  async function guideTask(
    id: string,
    content: string,
    confirmingTask?: string,
  ): Promise<TaskInput> {
    const taskId = confirmingTask || steeringTask(id)
    const previous = taskId ? inputSubmissions.value[taskId] : undefined
    const reason = steeringReason(id)
    if (!taskId || (reason && !(previous?.uncertain && confirmingTask === taskId)))
      throw new Error(reason || t('当前没有可引导的任务。'))
    content = content.trim()
    if (!content || content.length > 10000) throw new Error(t('补充内容需要 1–10000 个字符。'))
    if (previous && previous.content !== content)
      throw new Error(t('请先确认上一条补充的提交结果。'))
    const submission = previous || { key: requestKey(), content, uncertain: false }
    submission.uncertain = false
    inputSubmissions.value[taskId] = submission
    inputErrors.value[id] = ''
    const epoch = lifecycle
    try {
      const input = await api.submitTaskInput(taskId, content, submission.key)
      if (epoch === lifecycle && !deletedSessions.has(id)) {
        delete inputSubmissions.value[taskId]
        run.mergeInput(id, input)
        // A run may finish while the POST acknowledgement is in flight.
        if (run.turns[id]?.find((turn) => turn.id === taskId)?.status !== 'running')
          void refreshTaskDetails(id, taskId, true)
      }
      return input
    } catch (failure) {
      if (epoch === lifecycle && !deletedSessions.has(id)) {
        const refused =
          failure instanceof GatewayError && failure.status >= 400 && failure.status < 500
        if (refused) delete inputSubmissions.value[taskId]
        else inputSubmissions.value[taskId].uncertain = true
        inputErrors.value[id] =
          (failure instanceof Error ? failure.message : String(failure)) +
          (refused ? '' : t(' 提交结果未知，请确认，勿重复发送。'))
      }
      throw failure
    }
  }
  async function confirmInputSubmission(id: string, taskId: string) {
    const submission = inputSubmissions.value[taskId]
    if (submission?.uncertain) await guideTask(id, submission.content, taskId)
  }
  function queueMessage(id: string, content: string) {
    if (sessions.value.find((item) => item.id === id)?.archived)
      throw new Error(t('此对话已归档。'))
    const queue = (queuedMessages.value[id] ||= [])
    if (!queue.length) {
      const task = steeringTask(id)
      if (task) queuedAfterTask.set(id, task)
      queuedPaused.value[id] = !task || run.connected[id] === false
    }
    queue.push({ id: requestKey(), content })
  }
  function editQueued(id: string, entryId: string, content: string) {
    const item = queuedMessages.value[id]?.find((entry) => entry.id === entryId)
    if (item && content.trim()) item.content = content.trim()
  }
  function reorderQueued(id: string, entryId: string, targetId: string, position: 'before' | 'after') {
    const queue = queuedMessages.value[id]
    if (!queue || entryId === targetId) return
    const from = queue.findIndex(item => item.id === entryId)
    const target = queue.findIndex(item => item.id === targetId)
    if (from < 0 || target < 0) return
    const to = target + (position === 'after' ? 1 : 0) - (from < target ? 1 : 0)
    if (from === to) return
    const item = queue[from]
    queue.splice(from, 1)
    queue.splice(to, 0, item)
  }
  async function guideQueued(id: string, entryId: string) {
    const item = queuedMessages.value[id]?.find((entry) => entry.id === entryId)
    if (!item) return
    await guideTask(id, item.content)
    const index = queuedMessages.value[id]?.findIndex((entry) => entry.id === entryId) ?? -1
    if (index >= 0) removeQueued(id, index)
  }
  function refreshTaskDetails(id: string, taskId: string, fresh = false): Promise<void> {
    const key = `${id}:${taskId}`
    const existing = detailRequests.get(key)
    if (existing) {
      const epoch = lifecycle
      return fresh
        ? existing.promise.then(() => {
            if (epoch === lifecycle && !deletedSessions.has(id))
              return refreshTaskDetails(id, taskId)
          })
        : existing.promise
    }
    const epoch = lifecycle
    const controller = new AbortController()
    const readAfter = run.inputReadSequence(id, taskId)
    const current = () =>
      epoch === lifecycle && !deletedSessions.has(id) && !controller.signal.aborted
    const jobs: Promise<void>[] = []
    if (capabilities.value.task_input) {
      run.setDetailStatus(id, taskId, 'inputs', true)
      jobs.push(
        api
          .listTaskInputs(taskId, controller.signal)
          .then((inputs) => {
            if (current()) run.hydrateInputs(id, taskId, inputs, readAfter)
          })
          .then(() => {
            if (current()) run.setDetailStatus(id, taskId, 'inputs', false)
          })
          .catch(() => {
            if (current())
              run.setDetailStatus(id, taskId, 'inputs', false, t('补充状态未确认，请重试。'))
          }),
      )
    }
    if (capabilities.value.structured_plan && capabilities.value.task_inspection) {
      run.setDetailStatus(id, taskId, 'plan', true)
      jobs.push(
        api
          .getTaskInspection(taskId, controller.signal)
          .then((inspection) => {
            if (current()) {
              run.hydratePlan(id, inspection)
              const turn = run.turns[id]?.find((item) => item.id === taskId)
              run.setDetailStatus(
                id,
                taskId,
                'plan',
                false,
                inspection.projection.truncated &&
                  inspection.projection.plan === null &&
                  turn?.planSeq === undefined
                  ? t('较早的计划记录未完整加载，请重试。')
                  : '',
              )
            }
          })
          .catch(() => {
            if (current())
              run.setDetailStatus(id, taskId, 'plan', false, t('计划读取失败，请重试。'))
          }),
      )
    }
    const promise = Promise.all(jobs)
      .then(() => {})
      .finally(() => {
        if (detailRequests.get(key)?.controller === controller) detailRequests.delete(key)
      })
    detailRequests.set(key, { controller, promise })
    return promise
  }
  async function resendInput(id: string, taskId: string, inputId: string) {
    const item = run.turns[id]
      ?.find((turn) => turn.id === taskId)
      ?.inputs?.find((input) => input.id === inputId)
    if (
      !item ||
      item.status !== 'not_consumed' ||
      item.sentAsTaskId ||
      resendingInputs.has(inputId)
    )
      return
    if (runningTaskIds.value[id]?.length) throw new Error(t('当前任务结束后可作为新回合发送。'))
    resendingInputs.add(inputId)
    try {
      await sendToSession(id, item.content, { taskId, inputId })
    } finally {
      resendingInputs.delete(inputId)
    }
  }
  async function sendToSession(
    id: string,
    content: string,
    inputOrigin?: { taskId: string; inputId: string },
  ) {
    const previous = submissions.value[id]
    if (!previous && sessions.value.find((item) => item.id === id)?.archived)
      throw new Error(t('对话已归档，恢复后可发送消息。'))
    if (previous && !previous.uncertain) throw new Error(t('消息正在提交，请稍候。'))
    if (previous && previous.content !== content)
      throw new Error(t('上一条消息提交结果未知，请点击“确认提交结果”。'))
    if (
      previous &&
      inputOrigin &&
      (previous.inputOrigin?.taskId !== inputOrigin.taskId ||
        previous.inputOrigin?.inputId !== inputOrigin.inputId)
    )
      throw new Error(t('上一条消息提交结果未知，请点击“确认提交结果”。'))
    if (!previous && runningTaskIds.value[id]?.length) {
      throw new Error(t('任务正在运行，请选择引导或稍后发送。'))
    }
    watchSession(id)
    // getRandomValues also works on a private-network HTTP origin (unlike randomUUID).
    const submission = previous || {
      key: requestKey(),
      content,
      uncertain: false,
      inputOrigin,
    }
    submission.uncertain = false
    submissions.value[id] = submission
    const epoch = lifecycle
    const pending = 'pending-' + submission.key
    mark(id, pending)
    error.value = null
    if (queuedMessages.value[id]?.length && !queuedPaused.value[id])
      queuedAfterTask.set(id, pending)
    const optimistic: ChatMessage = {
      id: 'local-' + submission.key,
      conversation_id: id,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    ;(messages.value[id] ||= []).push(optimistic)
    let result: MessageReceipt
    try {
      result = await api.sendMessage(id, content, submission.key)
    } catch (reason) {
      if (epoch !== lifecycle || deletedSessions.has(id)) throw reason
      clear(id, pending)
      queuedPaused.value[id] = true
      messages.value[id] = (messages.value[id] || []).filter((m) => m.id !== optimistic.id)
      const refused = reason instanceof GatewayError && reason.status >= 400 && reason.status < 500
      if (refused) delete submissions.value[id]
      else submissions.value[id].uncertain = true
      error.value =
        (reason instanceof Error ? reason.message : String(reason)) +
        (refused ? '' : t(' 提交结果未知，请点击“确认提交结果”。'))
      throw reason
    }
    // Once acknowledged, a later snapshot failure is NOT a failed submission.
    delete submissions.value[id]
    if (epoch !== lifecycle || deletedSessions.has(id)) return
    clear(id, pending)
    // Confirmation of an ambiguous resend must close the same source action.
    if (submission.inputOrigin)
      run.markInputSent(
        id,
        submission.inputOrigin.taskId,
        submission.inputOrigin.inputId,
        result.task_id,
      )
    if (queuedAfterTask.get(id) === pending) queuedAfterTask.set(id, result.task_id)
    run.markStarted(id, result.task_id, content)
    const turn = run.getTurn(id, result.task_id)
    if (
      !['completed', 'failed', 'cancelled'].includes(turn.status) &&
      !['succeeded', 'failed', 'cancelled', 'interrupted', 'timed_out'].includes(result.status)
    )
      mark(id, result.task_id)
    else if (turn.status === 'completed' && !result.replayed) void sendNext(id, result.task_id)
    else queuedPaused.value[id] = true
    const item = sessions.value.find((s) => s.id === id)
    if (item) {
      item.updated_at = new Date().toISOString()
      if (item.title === '新对话') item.title = content.slice(0, 28)
      sessions.value.sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
    }
    try {
      await loadingSessions.get(id)
      if (epoch !== lifecycle || deletedSessions.has(id)) return
      await refreshSnapshot(id)
    } catch {
      error.value = t('消息已提交，状态更新失败。请重新打开对话，勿重复发送。')
    }
    return result
  }
  async function confirmSubmission(id: string) {
    const submission = submissions.value[id]
    if (!submission?.uncertain) return
    try {
      await sendToSession(id, submission.content)
    } catch {
      // A definitive refusal releases the key but must not lose the user's text.
      if (!deletedSessions.has(id) && !submissions.value[id] && !submission.inputOrigin) {
        const queue = (queuedMessages.value[id] ||= [])
        queue.unshift({ id: submission.key, content: submission.content })
        queuedPaused.value[id] = true
      }
    }
  }
  async function sendMessage(content: string) {
    if (activeSessionId.value) await sendToSession(activeSessionId.value, content)
  }
  async function sendNext(id: string, completedTask?: string, confirmed = false) {
    if (
      draining.has(id) ||
      submissions.value[id] ||
      runningTaskIds.value[id]?.length ||
      !queuedMessages.value[id]?.length
    )
      return
    if (
      !confirmed &&
      (queuedPaused.value[id] || !completedTask || queuedAfterTask.get(id) !== completedTask)
    )
      return
    draining.add(id)
    const epoch = lifecycle
    try {
      // HTTP 202 or an unrelated task's completion never authorizes a send.
      const tasks = await api.listTasks(id)
      if (epoch !== lifecycle) return
      if (
        tasks.some((t) =>
          ['created', 'queued', 'running', 'waiting_approval', 'retry_wait'].includes(t.status),
        )
      ) {
        queuedPaused.value[id] = true
        return
      }
      if (!confirmed && tasks.find((t) => t.id === completedTask)?.status !== 'succeeded') {
        queuedPaused.value[id] = true
        return
      }
      if (runningTaskIds.value[id]?.length || (!confirmed && queuedPaused.value[id])) return
      const next = queuedMessages.value[id]?.shift()
      if (!next) return
      queuedPaused.value[id] = false
      try {
        await sendToSession(id, next.content)
      } catch {
        // An ambiguous send already has a confirmation entry with the original key.
        if (!submissions.value[id]) (queuedMessages.value[id] ||= []).unshift(next)
        queuedPaused.value[id] = true
      }
    } catch (reason) {
      queuedPaused.value[id] = true
      error.value = String(reason)
    } finally {
      draining.delete(id)
      const task = queuedAfterTask.get(id)
      // A very short run can finish before its POST response arrives.
      if (
        task &&
        !queuedPaused.value[id] &&
        run.turns[id]?.find((t) => t.id === task)?.status === 'completed' &&
        !runningTaskIds.value[id]?.length &&
        queuedMessages.value[id]?.length
      ) {
        // The next turn will rebind the queue; do not retry a stale completion.
        if (task !== completedTask) void sendNext(id, task)
      }
    }
  }
  async function resumeQueued(id: string) {
    await sendNext(id, undefined, true)
  }
  function removeQueued(id: string, index: number) {
    queuedMessages.value[id]?.splice(index, 1)
  }
  async function setPermissionMode(permissionMode: PermissionMode, id = activeSessionId.value) {
    if (!id) return
    let updated: Conversation
    try {
      updated = await api.updateConversationPermission(id, permissionMode)
    } catch (reason) {
      // A timeout may follow a committed permission change. Read it back rather
      // than automatically retrying or displaying an optimistic downgrade.
      try {
        const current = await api.getConversation(id)
        const index = sessions.value.findIndex((item) => item.id === id)
        if (index >= 0) sessions.value[index] = current
      } catch { /* Keep the original error and block new submissions. */ }
      throw reason
    }
    const i = sessions.value.findIndex((s) => s.id === updated.id)
    if (i >= 0) sessions.value[i] = updated
  }
  async function setConversationRuntime(
    modelId: string,
    thinking: string,
    id = activeSessionId.value,
  ) {
    if (!id) return
    const updated = await api.updateConversationSettings(id, {
      model_id: modelId,
      thinking,
    })
    const i = sessions.value.findIndex((s) => s.id === updated.id)
    if (i >= 0) sessions.value[i] = updated
  }
  async function refreshEvents(id: string) {
    watchSession(id)
    await loadingSessions.get(id)
    await streams.get(id)?.recover()
  }
  async function setSubagentLimits(
    calls: number,
    concurrent: number,
    id = activeSessionId.value,
    modelIds?: string[],
  ) {
    if (!id) return
    const updated = await api.updateConversationSettings(id, {
      max_subagent_calls: calls,
      max_concurrent_subagents: concurrent,
      ...(modelIds === undefined ? {} : { subagent_model_ids: modelIds }),
    })
    const i = sessions.value.findIndex((s) => s.id === updated.id)
    if (i >= 0) sessions.value[i] = updated
  }
  async function decideApproval(approval: Approval, decision: 'allow' | 'deny') {
    await api.decideApproval(approval.id, decision, approval.version)
    await refreshEvents(approval.conversation_id)
  }
  function interruptedTask(id: string): AgentTask | undefined {
    const list = tasks.value[id] || []
    const latest = list[list.length - 1]
    return latest?.status === 'interrupted' ? latest : undefined
  }
  function canResumeTask(id: string) {
    return Boolean(
      capabilities.value.task_resume &&
      interruptedTask(id) &&
      !sessions.value.find((item) => item.id === id)?.archived &&
      !runningTaskIds.value[id]?.length &&
      !submissions.value[id],
    )
  }
  async function resumeInterruptedTask(id: string) {
    const task = interruptedTask(id)
    if (!task || !canResumeTask(id) || resumingTaskIds.value.includes(task.id)) return
    resumingTaskIds.value.push(task.id)
    queuedPaused.value[id] = true
    error.value = null
    const epoch = lifecycle
    try {
      await refreshSnapshot(id)
      if (epoch !== lifecycle || deletedSessions.has(id)) return
      const current = interruptedTask(id)
      if (!canResumeTask(id) || current?.id !== task.id)
        throw new Error(t('任务状态已变化，请查看最新进展。'))
      // Checkpoint eligibility and all permission checks remain on the server.
      await api.resumeTask(current.id, current.version)
      if (epoch !== lifecycle || deletedSessions.has(id)) return
      await refreshEvents(id)
      await refreshSnapshot(id)
    } catch (reason) {
      if (epoch === lifecycle && !deletedSessions.has(id)) {
        error.value =
          t('未能确认恢复：') + (reason instanceof Error ? reason.message : String(reason))
        // A timeout may have happened after acceptance. Reconcile, never re-submit.
        try {
          await refreshSnapshot(id)
        } catch {
          /* Keep the original error. */
        }
      }
    } finally {
      resumingTaskIds.value = resumingTaskIds.value.filter((item) => item !== task.id)
    }
  }
  async function cancelTask(task: string, conversationId: string) {
    queuedPaused.value[conversationId] = true
    if (stoppingTaskIds.value.includes(task)) return false
    stoppingTaskIds.value.push(task)
    try {
      await api.cancelTask(task)
      // HTTP acceptance is not a terminal task event.
      await refreshEvents(conversationId)
      return true
    } catch (reason) {
      error.value =
        t('未能确认停止：') + (reason instanceof Error ? reason.message : String(reason))
      return false
    } finally {
      stoppingTaskIds.value = stoppingTaskIds.value.filter((id) => id !== task)
    }
  }
  async function cancelActiveTask() {
    const id = activeSessionId.value
    if (id) queuedPaused.value[id] = true
    const task = id ? runningTaskIds.value[id]?.find((t) => !t.startsWith('pending-')) : undefined
    if (id && task) await cancelTask(task, id)
    else if (id && runningTaskIds.value[id]?.length)
      error.value = t('消息正在提交，任务建立后可停止。待发消息已暂停。')
  }
  function disconnect() {
    generation++
    lifecycle++
    installingSnapshots.clear()
    snapshotFacts.clear()
    taskSequences.clear()
    for (const request of detailRequests.values()) request.controller.abort()
    detailRequests.clear()
    for (const submission of Object.values(inputSubmissions.value)) submission.uncertain = true
    loadingSessions.clear()
    for (const [id, submission] of Object.entries(submissions.value)) {
      submission.uncertain = true
      clear(id, 'pending-' + submission.key)
    }
    for (const id of Object.keys(queuedMessages.value)) queuedPaused.value[id] = true
    for (const stream of streams.values()) stream.stop()
    streams.clear()
  }
  async function manageSession(
    id: string,
    patch: { title?: string; archived?: boolean; folder_id?: string; memory_enabled?: boolean },
  ) {
    const item = retainedWorkspace(await api.updateConversationSettings(id, patch))
    const index = sessions.value.findIndex((session) => session.id === id)
    if (index >= 0) sessions.value[index] = item
    if (activeSessionId.value === id) workspace.select(item.folder_id || '')
    return item
  }
  async function deleteSession(id: string) {
    await api.deleteConversation(id)
    // Late snapshots/events must not recreate a conversation after confirmed deletion.
    deletedSessions.add(id)
    loadingSessions.delete(id)
    streams.get(id)?.stop()
    streams.delete(id)
    installingSnapshots.delete(id)
    snapshotFacts.delete(id)
    for (const key of taskSequences.keys()) if (key.startsWith(`${id}:`)) taskSequences.delete(key)
    for (const [key, request] of detailRequests)
      if (key.startsWith(`${id}:`)) {
        request.controller.abort()
        detailRequests.delete(key)
      }
    for (const turn of run.turns[id] || []) delete inputSubmissions.value[turn.id]
    delete inputErrors.value[id]
    for (const map of [
      messages.value,
      approvals.value,
      runningTaskIds.value,
      queuedMessages.value,
      queuedPaused.value,
      tasks.value,
      submissions.value,
      historyTruncated.value,
    ])
      delete map[id]
    queuedAfterTask.delete(id)
    sessions.value = sessions.value.filter((item) => item.id !== id)
    run.forgetSession(id)
    if (activeSessionId.value === id) activeSessionId.value = null
  }
  onScopeDispose(disconnect)
  return {
    sessions,
    visibleSessions,
    showArchived,
    messages,
    approvals,
    activities,
    taskActivityStatuses,
    runningTaskIds,
    queuedMessages,
    queuedPaused,
    inputSubmissions,
    inputErrors,
    activeSessionId,
    isLoading,
    isSending,
    isStopping,
    stoppingTaskIds,
    submissions,
    historyTruncated,
    capabilities,
    resumingTaskIds,
    error,
    activeSession,
    activeMessages,
    activeApprovals,
    activeActivities,
    activeActivityGroups,
    loadSessions,
    createSession,
    forkSession,
    manageSession,
    deleteSession,
    moveWorkspaceSessionsToDefault,
    switchSession,
    watchSession,
    refreshEvents,
    refreshSnapshot,
    sendMessage,
    sendToSession,
    guideTask,
    steeringTask,
    steeringReason,
    confirmInputSubmission,
    queueMessage,
    editQueued,
    reorderQueued,
    guideQueued,
    refreshTaskDetails,
    resendInput,
    setPermissionMode,
    setConversationRuntime,
    setSubagentLimits,
    decideApproval,
    cancelActiveTask,
    cancelTask,
    removeQueued,
    resumeQueued,
    confirmSubmission,
    interruptedTask,
    canResumeTask,
    resumeInterruptedTask,
    disconnect,
  }
})
