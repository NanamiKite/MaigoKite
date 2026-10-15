import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import { api, type AgentEvent, type AgentTask, type ConversationSnapshot } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useRunStore } from '@/stores/run'

describe('session store', () => {
  let onEvent: ((event: AgentEvent) => void) | undefined
  let snapshot: ConversationSnapshot
  const task = (id: string, status: AgentTask['status'] = 'running'): AgentTask => ({
    id,
    workspace_id: 'personal',
    conversation_id: 'conv-1',
    title: 'task',
    status,
    input_text: 'work',
    last_error: '',
    version: 1,
    created_at: '2026-09-18T00:00:00Z',
    updated_at: '2026-09-18T00:00:01Z',
  })
  async function ready() {
    const store = useSessionStore()
    await store.switchSession('conv-1')
    onEvent!({
      schema_version: 1,
      event_id: 'ready',
      sequence: 0,
      type: 'stream.ready',
      conversation_id: 'conv-1',
      payload: {},
    })
    await flushPromises()
    return store
  }
  const publish = () => vi.advanceTimersByTimeAsync(20)

  beforeEach(() => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
    vi.useFakeTimers()
    snapshot = {
      messages: [],
      tasks: [],
      approvals: [],
      questions: [],
      last_sequence: 0,
      truncated: false,
    }
    vi.spyOn(api, 'getConversation').mockResolvedValue({
      id: 'conv-1',
      workspace_id: 'personal',
      title: 't',
      model_id: 'model-1',
      permission_mode: 'read_only',
      response_mode: 'balanced',
      created_at: '',
      updated_at: '',
      message_count: 0,
    })
    vi.spyOn(api, 'getConversationSnapshot').mockImplementation(async () =>
      structuredClone(snapshot),
    )
    vi.spyOn(api, 'getCapabilities').mockResolvedValue({})
    vi.spyOn(api, 'listTaskEvents').mockResolvedValue([])
    vi.spyOn(api, 'connectEvents').mockImplementation((_id, callback) => {
      onEvent = callback
      return { close: vi.fn(), readyState: 1 } as unknown as WebSocket
    })
  })

  it('tracks a durable approval and submits its versioned decision', async () => {
    vi.spyOn(api, 'decideApproval').mockResolvedValue({ task_id: 'task-approval' })
    const store = await ready()
    onEvent?.({
      schema_version: 1,
      event_id: 'a1',
      sequence: 1,
      type: 'approval.requested',
      conversation_id: 'conv-1',
      task_id: 'task-approval',
      payload: {
        approval_id: 'approval-1',
        workspace_id: 'personal',
        conversation_id: 'conv-1',
        task_id: 'task-approval',
        tool_call_id: 'call-1',
        tool_name: 'file_write',
        arguments: { path: '/workspace/a.txt' },
        resource_scope: '/workspace/a.txt',
        risk_level: 'L4',
        status: 'pending',
        version: 1,
        expires_at: '2099-01-01T00:00:00Z',
      },
    })
    await publish()
    expect(store.activeApprovals).toHaveLength(1)
    await store.decideApproval(store.activeApprovals[0], 'allow')
    expect(api.decideApproval).toHaveBeenCalledWith('approval-1', 'allow', 1)
  })

  it('shows a task as waiting until its approval is allowed', async () => {
    const store = await ready()
    onEvent?.({
      schema_version: 1,
      event_id: 'wait-1',
      sequence: 1,
      type: 'task.waiting_approval',
      conversation_id: 'conv-1',
      task_id: 'task-waiting',
      payload: { tool_call_id: 'call-1', approval_id: 'approval-1' },
    })
    await publish()
    expect(store.activeActivityGroups[0].status).toBe('waiting_approval')
    expect(store.isSending).toBe(true)
    expect(store.activeApprovals).toEqual([])
    expect(useRunStore().getTurn('conv-1', 'task-waiting').awaits).toEqual([])

    onEvent?.({
      schema_version: 1,
      event_id: 'wait-2',
      sequence: 2,
      type: 'approval.decided',
      conversation_id: 'conv-1',
      task_id: 'task-waiting',
      payload: { approval_id: 'approval-1', decision: 'allow' },
    })
    await publish()
    expect(store.activeActivityGroups[0].status).toBe('running')
  })

  it('keeps structured recovery guidance on failed tool activity', async () => {
    const store = await ready()
    onEvent?.({
      schema_version: 1,
      event_id: 'tool-failed-1',
      sequence: 1,
      type: 'tool.failed',
      conversation_id: 'conv-1',
      task_id: 'task-search',
      payload: {
        tool_call_id: 'call-search',
        tool_name: 'web_search',
        message: 'Web search provider request failed.',
        code: 'SEARCH_PROVIDER_UNAVAILABLE',
        user_hint: '请检查网络，或配置其他搜索服务。',
        action: { label: '配置联网工具', to: '/settings?tab=tools' },
        data: { results: [], error: 'search provider request failed' },
      },
    })

    await publish()
    // Inspect the same Step consumed by the production Rail, not legacy computed facades.
    expect(useRunStore().getTurn('conv-1', 'task-search').steps[0]).toMatchObject({
      status: 'failed',
      hint: '请检查网络，或配置其他搜索服务。',
      action: { label: '设置工具', to: '/settings?tab=tools' },
    })
    expect(JSON.parse(useRunStore().getTurn('conv-1', 'task-search').steps[0].output)).toEqual({
      results: [],
      error: 'search provider request failed',
    })
    expect(store.activeMessages).toEqual([])
  })

  it('adds an optimistic message and completes a streamed reply', async () => {
    let acknowledge!: () => void
    const receipt = new Promise<void>((resolve) => {
      acknowledge = resolve
    })
    vi.spyOn(api, 'sendMessage').mockImplementation(async () => {
      await receipt
      snapshot.tasks = [task('task-1')]
      snapshot.messages = [
        { id: 'user-1', conversation_id: 'conv-1', role: 'user', content: 'hello', created_at: '' },
      ]
      return { task_id: 'task-1', status: 'processing' }
    })
    const store = useSessionStore()
    store.sessions.push({
      id: 'conv-1',
      workspace_id: 'personal',
      title: 't',
      model_id: '',
      permission_mode: 'read_only',
      response_mode: 'balanced',
      created_at: '',
      updated_at: '',
      message_count: 0,
    })
    await ready()
    const sending = store.sendMessage('hello')
    expect(store.activeMessages[0]).toMatchObject({
      id: expect.stringMatching(/^local-/),
      content: 'hello',
      role: 'user',
    })
    expect(store.isSending).toBe(true)
    acknowledge()
    await sending
    expect(store.activeMessages).toHaveLength(1)
    expect(store.activeMessages[0].id).toBe('user-1')
    expect(store.activeMessages[0].content).toBe('hello')
    onEvent?.({
      schema_version: 1,
      event_id: '1',
      sequence: 1,
      type: 'message.delta',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { content: 'world' },
    })
    onEvent?.({
      schema_version: 1,
      event_id: '2',
      sequence: 2,
      type: 'message.completed',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { message_id: 'answer-1', content: 'world' },
    })
    await publish()
    expect(store.activeMessages[1]).toMatchObject({
      id: 'answer-1',
      role: 'assistant',
      content: 'world',
    })
    expect(store.activeMessages[1]).not.toHaveProperty('streaming')
    expect(useRunStore().getTurn('conv-1', 'task-1').text).toBe('world')
    expect(store.isSending).toBe(true)
    snapshot.tasks = [task('task-1', 'succeeded')]
    snapshot.messages.push({
      id: 'answer-1',
      conversation_id: 'conv-1',
      role: 'assistant',
      content: 'world',
      created_at: '',
    })
    snapshot.last_sequence = 3
    onEvent!({
      schema_version: 1,
      event_id: '3',
      sequence: 3,
      type: 'task.succeeded',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: {},
    })
    await publish()
    expect(store.isSending).toBe(false)
    expect(store.activeMessages.map((message) => message.content)).toEqual(['hello', 'world'])
    expect(api.sendMessage).toHaveBeenCalledWith(
      'conv-1',
      'hello',
      expect.stringMatching(/^[a-f0-9]{32}$/),
      [],
    )
  })

  it('restores activity from durable events and cancels the active task', async () => {
    snapshot.tasks = [
      {
        id: 'task-running',
        workspace_id: 'personal',
        conversation_id: 'conv-1',
        title: 'task',
        status: 'running',
        input_text: 'work',
        last_error: '',
        version: 1,
        created_at: '2026-09-18T00:00:00Z',
        updated_at: '2026-09-18T00:00:01Z',
      },
    ]
    vi.mocked(api.listTaskEvents).mockResolvedValueOnce([
      {
        schema_version: 1,
        event_id: 'persisted-1',
        sequence: 1,
        type: 'tool.started',
        conversation_id: 'conv-1',
        task_id: 'task-running',
        payload: { tool_call_id: 'call-1', tool_name: 'browser' },
      },
    ])
    vi.spyOn(api, 'cancelTask').mockResolvedValue({
      id: 'task-running',
      workspace_id: 'personal',
      conversation_id: 'conv-1',
      title: 'task',
      status: 'cancelled',
      input_text: 'work',
      last_error: 'Cancelled by user',
      version: 2,
      created_at: '',
      updated_at: '',
    })
    const store = await ready()
    await publish()
    expect(store.isSending).toBe(true)
    expect(store.activeActivityGroups[0].items[0].title).toBe('browser')

    await store.cancelActiveTask()
    expect(api.cancelTask).toHaveBeenCalledWith('task-running')
    // An accepted HTTP stop is not proof that the runtime has finished cleanup.
    expect(store.isSending).toBe(true)
    snapshot.tasks = [task('task-running', 'cancelled')]
    snapshot.last_sequence = 2
    onEvent!({
      schema_version: 1,
      event_id: 'cancelled',
      sequence: 2,
      type: 'task.cancelled',
      conversation_id: 'conv-1',
      task_id: 'task-running',
      payload: {},
    })
    await publish()
    expect(store.isSending).toBe(false)
  })

  it('restores a completed message by its durable message id without duplication', async () => {
    snapshot.messages = [
      {
        id: 'message-1',
        conversation_id: 'conv-1',
        role: 'assistant',
        content: '已完成',
        created_at: '2026-09-18T00:00:00Z',
      },
    ]
    snapshot.last_sequence = 3
    vi.mocked(api.listTaskEvents).mockResolvedValueOnce([
      {
        schema_version: 1,
        event_id: 'completed-1',
        sequence: 3,
        type: 'message.completed',
        conversation_id: 'conv-1',
        task_id: 'task-1',
        occurred_at: '2026-09-18T00:00:00Z',
        payload: { message_id: 'message-1', content: '已完成' },
      },
    ])
    const store = await ready()
    expect(store.activeMessages).toHaveLength(1)
    expect(store.activeMessages[0]).toMatchObject({ id: 'message-1', content: '已完成' })
  })

  it('keeps reasoning, tools and subagents out of the assistant reply', async () => {
    const store = await ready()

    onEvent?.({
      schema_version: 1,
      event_id: '1',
      sequence: 1,
      type: 'task.started',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: {},
    })
    onEvent?.({
      schema_version: 1,
      event_id: '2',
      sequence: 2,
      type: 'reasoning.completed',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { activity_id: 'reasoning-1', summary: '先检查项目文件。' },
    })
    onEvent?.({
      schema_version: 1,
      event_id: '3',
      sequence: 3,
      type: 'tool.proposed',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { tool_call_id: 'call-1', tool_name: 'file_read', arguments: { path: 'README.md' } },
    })
    onEvent?.({
      schema_version: 1,
      event_id: '4',
      sequence: 4,
      type: 'tool.completed',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { tool_call_id: 'call-1', tool_name: 'file_read', summary: '读取完成' },
    })
    onEvent?.({
      schema_version: 1,
      event_id: '5',
      sequence: 5,
      type: 'subagent.started',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { subagent_id: 'reviewer', name: 'Reviewer', summary: '检查实现' },
    })
    onEvent?.({
      schema_version: 1,
      event_id: '6',
      sequence: 6,
      type: 'subagent.completed',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { subagent_id: 'reviewer', name: 'Reviewer', summary: '检查完成' },
    })

    await publish()
    expect(store.activeMessages).toEqual([])
    const turn = useRunStore().getTurn('conv-1', 'task-1')
    expect(turn.thinking).toBe('先检查项目文件。')
    expect(turn.steps.map((step) => [step.id, step.tool, step.status])).toEqual([
      ['call-1', 'file_read', 'completed'],
      ['reviewer', 'delegate', 'completed'],
    ])
    expect(turn.steps[1].output).toBe('检查完成')

    onEvent?.({
      schema_version: 1,
      event_id: '7',
      sequence: 7,
      type: 'message.delta',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { content: '这是最终回答。' },
    })
    onEvent?.({
      schema_version: 1,
      event_id: '8',
      sequence: 8,
      type: 'message.completed',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: { message_id: 'final', content: '这是最终回答。' },
    })

    await publish()
    expect(store.activeMessages).toHaveLength(1)
    expect(store.activeMessages[0].content).toBe('这是最终回答。')
    expect(store.activeMessages[0].content).not.toContain('先检查项目文件')
    expect(store.isSending).toBe(true)
    snapshot.tasks = [task('task-1', 'succeeded')]
    snapshot.messages = [
      {
        id: 'final',
        conversation_id: 'conv-1',
        role: 'assistant',
        content: '这是最终回答。',
        created_at: '',
      },
    ]
    snapshot.last_sequence = 9
    onEvent!({
      schema_version: 1,
      event_id: '9',
      sequence: 9,
      type: 'task.succeeded',
      conversation_id: 'conv-1',
      task_id: 'task-1',
      payload: {},
    })
    await publish()
    expect(store.activeActivityGroups[0].status).toBe('completed')
  })

  it('persists the selected model and thinking depth together', async () => {
    vi.spyOn(api, 'updateConversationSettings').mockResolvedValue({
      id: 'conv-1',
      workspace_id: 'personal',
      title: 't',
      model_id: 'model-2',
      permission_mode: 'read_only',
      response_mode: 'balanced',
      thinking: 'high',
      created_at: '',
      updated_at: '',
      message_count: 0,
    })
    const store = useSessionStore()
    store.sessions.push({
      id: 'conv-1',
      workspace_id: 'personal',
      title: 't',
      model_id: 'model-1',
      permission_mode: 'read_only',
      response_mode: 'balanced',
      created_at: '',
      updated_at: '',
      message_count: 0,
    })
    store.activeSessionId = 'conv-1'
    await store.setConversationRuntime('model-2', 'high')
    expect(api.updateConversationSettings).toHaveBeenCalledWith('conv-1', {
      model_id: 'model-2',
      thinking: 'high',
    })
    expect(store.activeSession?.model_id).toBe('model-2')
  })

  it('does not refetch or reconnect an already active session', async () => {
    const snapshotRead = vi.mocked(api.getConversationSnapshot)
    const connectEvents = vi.mocked(api.connectEvents)
    const store = useSessionStore()

    await store.switchSession('conv-1')
    await store.switchSession('conv-1')

    expect(snapshotRead).toHaveBeenCalledTimes(1)
    expect(api.getConversation).toHaveBeenCalledTimes(1)
    expect(connectEvents).toHaveBeenCalledTimes(1)
  })
})
