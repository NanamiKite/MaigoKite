import type {
  AgentEvent,
  AgentTask,
  Approval,
  ChatMessage,
  Conversation,
  UserMemory,
  WorkspaceFileContent,
  WorkspaceFolder,
} from './client'
import { mockEnvelope, playTask } from './mock-playback'
import { t } from '@/i18n'

const now = () => new Date().toISOString()
const folders: WorkspaceFolder[] = [{ id: '', name: '默认工作区', root_path: '/demo/workspace' }]
const conversationDefaults = {
  thinking: '',
  folder_id: '',
  archived: false,
  memory_enabled: false,
  max_subagent_calls: 0,
  max_concurrent_subagents: 1,
  subagent_model_ids: [],
  forked_from_conversation_id: '',
  forked_from_message_id: '',
}
const conversations: Conversation[] = [
  {
    ...conversationDefaults,
    id: 'inkline-demo',
    workspace_id: 'personal',
    title: '整理一个想法',
    model_id: 'demo',
    permission_mode: 'read_only',
    response_mode: 'balanced',
    created_at: now(),
    updated_at: now(),
    message_count: 0,
  },
]
const messages = new Map<string, ChatMessage[]>(),
  events = new Map<string, AgentEvent[]>()
const subscribers = new Map<string, Set<(event: AgentEvent) => void>>()
const tasks: AgentTask[] = [],
  approvals: Approval[] = [],
  memories: UserMemory[] = []
const resumes = new Map<string, (allowed: boolean, note?: string) => void>(),
  timers = new Map<string, Set<ReturnType<typeof setTimeout>>>()
const submissions = new Map<string, { content: string; taskId: string }>()
const forks = new Map<string, { messageId: string; conversation: Conversation }>()
const memorySettings = new Map<string, boolean>()
const model = {
  id: 'demo',
  name: '本地预览',
  provider: 'openai_compatible',
  model: 'demo',
  base_url: 'http://localhost:1234/v1',
  temperature: 0.7,
  max_tokens: 4096,
  timeout_seconds: 60,
  credential_configured: false,
  is_default: true,
}
let assistantName = '小墨'
function emit(sessionId: string, taskId: string, type: string, payload: Record<string, unknown>) {
  const history = events.get(sessionId) || []
  const event = mockEnvelope(sessionId, taskId, history.length + 1, type, payload)
  history.push(event)
  events.set(sessionId, history)
  subscribers.get(sessionId)?.forEach((receive) => receive(event))
}
function stopTimers(taskId: string) {
  timers.get(taskId)?.forEach((timer) => clearTimeout(timer))
  timers.delete(taskId)
}
function start(sessionId: string, content: string) {
  const id = `demo-${Date.now()}`
  const task: AgentTask = {
    subagent_model_ids: [],
    output_mode: 'text',
    result_ready: false,
    id,
    workspace_id: 'personal',
    conversation_id: sessionId,
    title: content.slice(0, 28),
    status: 'running',
    input_text: content,
    last_error: '',
    version: 1,
    created_at: now(),
    updated_at: now(),
  }
  tasks.push(task)
  const list = messages.get(sessionId) || []
  list.push({
    id: `user-${id}`,
    conversation_id: sessionId,
    role: 'user',
    content,
    created_at: now(),
  })
  messages.set(sessionId, list)
  emit(sessionId, id, 'task.started', { input_text: content, trace_id: id })
  const wait = (delay: number, action: () => void) => {
    const set = timers.get(id) || new Set<ReturnType<typeof setTimeout>>()
    const timer = setTimeout(() => {
      set.delete(timer)
      if (task.status !== 'cancelled') action()
    }, delay)
    set.add(timer)
    timers.set(id, set)
  }
  playTask({
    wait,
    emit: (type, payload) => emit(sessionId, id, type, payload),
    approval: (resume) => {
      const approval: Approval = {
        id: `approval-${id}`,
        workspace_id: 'personal',
        conversation_id: sessionId,
        task_id: id,
        tool_call_id: 'write',
        tool_name: 'file_write',
        arguments: { path: 'notes/next-step.md' },
        resource_scope: 'notes/next-step.md',
        risk_level: 'L4',
        status: 'pending',
        version: 1,
        expires_at: '2099-01-01T00:00:00Z',
        note: '',
        decided_at: '',
        allowed_scopes: ['once'],
        reuse_scope: {},
        authorization_scope: 'once',
        authorization_expires_at: '',
        authorization_revoked_at: '',
      }
      task.status = 'waiting_approval'
      approvals.push(approval)
      resumes.set(approval.id, resume)
      emit(sessionId, id, 'approval.requested', { ...approval, approval_id: approval.id })
    },
    remember: () => {
      const memory: UserMemory = {
        id: `memory-${id}`,
        workspace_id: 'personal',
        subject_user_id: 'local-user',
        category: 'preference',
        content: '偏好简短的任务清单。',
        source_type: 'agent',
        source_id: sessionId,
        confidence: 1,
        created_at: now(),
        updated_at: now(),
      }
      memories.push(memory)
      emit(sessionId, id, 'memory.added', { memory_id: memory.id, content: memory.content })
    },
    finish: (answer) => {
      const wroteFile = approvals.some(
        (approval) => approval.task_id === id && approval.status === 'consumed',
      )
      list.push({
        id: `answer-${id}`,
        conversation_id: sessionId,
        role: 'assistant',
        content: answer,
        created_at: now(),
      })
      emit(sessionId, id, 'message.completed', { message_id: `answer-${id}`, content: answer })
      task.status = 'succeeded'
      task.updated_at = now()
      emit(sessionId, id, 'task.succeeded', {
        summary: {
          files: wroteFile ? 1 : 0,
          added: wroteFile ? 2 : 0,
          removed: 0,
          commands: 1,
          failed: wroteFile ? 0 : 1,
          awaits: 1,
          contextPct: 18,
          durationMs: Date.now() - Date.parse(task.created_at),
        },
      })
    },
  })
  return { task_id: id, status: 'processing' }
}
export function mockConnect(id: string, receive: (event: AgentEvent) => void): WebSocket {
  const listeners = subscribers.get(id) || new Set<(event: AgentEvent) => void>()
  listeners.add(receive)
  subscribers.set(id, listeners)
  const socket = {
    readyState: 0,
    onopen: null as ((event: Event) => void) | null,
    onclose: null as ((event: Event) => void) | null,
    onerror: null,
    close() {
      socket.readyState = 3
      listeners.delete(receive)
      socket.onclose?.(new Event('close'))
    },
  }
  setTimeout(() => {
    if (socket.readyState !== 3) {
      socket.readyState = 1
      socket.onopen?.(new Event('open'))
      receive({ type: 'stream.ready', payload: {} } as AgentEvent)
    }
  }, 0)
  return socket as unknown as WebSocket
}
export async function mockRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const parsed = new URL(url, 'http://mock.local'),
    parts = parsed.pathname.split('/'),
    id = decodeURIComponent(parts[parts.length - 1])
  const method = init?.method || 'GET',
    body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {}
  // Never report a synthetic listing as a successful real filesystem mutation.
  if (
    method === 'POST' &&
    (parsed.pathname.endsWith('/files') || parsed.pathname.endsWith('/files/open-folder'))
  )
    throw new Error(t('当前环境不支持创建文件或打开文件夹。'))
  let result: unknown
  if (url === '/health') result = { status: 'ok', version: 'demo' }
  else if (parsed.pathname.endsWith('/capabilities'))
    // The demo does not implement durable recovery; never advertise a fake resume action.
    result = { capabilities: { task_resume: false, conversation_fork: true } }
  else if (parsed.pathname.endsWith('/preferences')) {
    if (method === 'PATCH') assistantName = String(body.assistant_name)
    result = { assistant_name: assistantName }
  } else if (parsed.pathname.endsWith('/runtime-status'))
    result = {
      workspace_id: 'personal',
      workspace_root:
        folders.find((folder) => folder.id === (parsed.searchParams.get('folder_id') || ''))
          ?.root_path || '/demo/workspace',
      workspace_source: parsed.searchParams.get('folder_id') ? 'selection' : 'configuration',
      environment: 'demo',
      backend: { status: 'serving' },
      restart: { pending: false, required_for: [], hot_applied: [], note: '' },
    }
  else if (parsed.pathname.endsWith('/folders/browse')) {
    // The existing demo stays isolated from native dialogs and real directories.
    const current = (parsed.searchParams.get('path') || '/demo').replace(/\/+$/, '') || '/'
    const prefix = current === '/' ? '/' : current + '/'
    const children = new Set<string>()
    for (const folder of folders) {
      if (folder.root_path.startsWith(prefix)) {
        const name = folder.root_path.slice(prefix.length).split('/')[0]
        if (name) children.add(name)
      }
    }
    result = {
      path: current,
      parent: current === '/' ? '' : current.slice(0, current.lastIndexOf('/')) || '/',
      home: '/demo',
      root: '/',
      truncated: false,
      entries: [...children].map((name) => ({ name, path: prefix + name })),
    }
  } else if (parsed.pathname.endsWith('/folders')) {
    if (method === 'POST') {
      const root = String(body.root_path)
      const existing = folders.find((folder) => folder.root_path === root)
      const item = existing || {
        id: `folder-${Date.now()}`,
        root_path: root,
        name: String(body.name || root.split(/[\\/]/).filter(Boolean).pop() || '工作区'),
      }
      if (!existing) folders.push(item)
      result = item
    } else result = { folders }
  } else if (parsed.pathname.includes('/folders/') && method === 'DELETE') {
    const index = folders.findIndex((folder) => folder.id === id)
    if (!id || index < 0) throw new Error(t('工作区不存在。'))
    if (
      conversations.some(
        (item) =>
          item.folder_id === id &&
          tasks.some(
            (task) =>
              task.conversation_id === item.id &&
              [
                'created',
                'queued',
                'running',
                'waiting_approval',
                'retry_wait',
                'interrupted',
              ].includes(task.status),
          ),
      )
    )
      throw new Error(t('工作区中有未结束的任务，请完成或停止后再删除。'))
    for (const item of conversations) if (item.folder_id === id) item.folder_id = ''
    folders.splice(index, 1)
    result = undefined
  } else if (parsed.pathname.endsWith('/permissions'))
    result = { default_mode: body.default_mode || 'workspace' }
  else if (parsed.pathname.includes('/models/actions/validate'))
    result = { ok: true, message: '本地预览', model: 'demo' }
  else if (parsed.pathname.includes('/models/actions/discover'))
    result = { models: [{ id: 'demo' }] }
  else if (parsed.pathname.endsWith('/models/actions/batch'))
    throw new Error(t('此操作不可用，请重启或更新应用。'))
  else if (parsed.pathname.endsWith('/thinking'))
    result = {
      kind: 'effort',
      options: ['low', 'medium', 'high'],
      default: 'medium',
      source: 'mock',
      reason: '',
    }
  else if (parsed.pathname.endsWith('/models') || parsed.pathname.endsWith('/default'))
    result = { models: [model], default_model_id: 'demo', revision: '0'.repeat(64) }
  else if (parsed.pathname.includes('/models/')) result = { ...model, ...body }
  else if (parsed.pathname.endsWith('/settings')) result = { models: { chat: model } }
  else if (parsed.pathname.includes('/credential-references')) result = { engines: [] }
  else if (parsed.pathname.endsWith('/tool-settings')) result = { tools: [] }
  else if (parsed.pathname.endsWith('/mcp-servers')) result = { servers: [] }
  else if (parsed.pathname.endsWith('/extension-settings') && method === 'GET')
    result = {
      config: {
        revision: 0,
        mcp_servers: [],
        skills: [],
        skill_directories: [],
        disabled_skills: [],
      },
      applied_revision: 0,
      reload_required: false,
      reloading: false,
      runtime_ready: true,
      servers: [],
      skills: [],
      skill_errors: [],
      management_available: false,
    }
  else if (parsed.pathname.includes('/extension-settings'))
    throw new Error(t('扩展管理不可用，请检查服务连接。'))
  else if (parsed.pathname.endsWith('/skills')) result = { skills: [] }
  else if (parsed.pathname.endsWith('/audit-events')) result = { events: [] }
  else if (parsed.pathname.endsWith('/data-summary'))
    result = {
      storage_backend: 'demo',
      conversation_count: conversations.length,
      message_count: [...messages.values()].flat().length,
      memory_count: memories.length,
      task_count: tasks.length,
      audit_event_count: 0,
    }
  else if (parsed.pathname.endsWith('/memory-settings')) {
    const folder = String(
      method === 'PATCH' ? body.folder_id : parsed.searchParams.get('folder_id') || '',
    )
    if (method === 'PATCH') memorySettings.set(folder, body.enabled === true)
    result = { folder_id: folder, enabled: memorySettings.get(folder) === true }
  } else if (parsed.pathname.includes('/memories')) {
    if (method === 'GET')
      result = {
        memories: memories.filter(
          (m) =>
            (m.folder_scope ?? null) === parsed.searchParams.get('folder_id') &&
            m.content
              .toLocaleLowerCase()
              .includes((parsed.searchParams.get('q') || '').toLocaleLowerCase()),
        ),
      }
    else if (method === 'DELETE') {
      const index = memories.findIndex((m) => m.id === id)
      if (index >= 0) memories.splice(index, 1)
    } else {
      const existing = memories.find((m) => m.id === id)
      const memory: UserMemory = {
        id: existing?.id || `memory-${Date.now()}`,
        workspace_id: 'personal',
        subject_user_id: 'local-user',
        folder_scope:
          typeof body.folder_id === 'string' ? body.folder_id : (existing?.folder_scope ?? null),
        category: String(body.category) as UserMemory['category'],
        content: String(body.content),
        source_type: existing?.source_type || 'user',
        source_id: existing?.source_id || '',
        confidence: Number(body.confidence),
        created_at: existing?.created_at || now(),
        updated_at: now(),
      }
      if (existing) Object.assign(existing, memory)
      else memories.push(memory)
      result = memory
    }
  } else if (parsed.pathname.endsWith('/questions')) result = { questions: [] }
  else if (parsed.pathname.endsWith('/answer') && parts.includes('questions'))
    throw new Error('没有待回答的提问。')
  else if (parsed.pathname.endsWith('/approvals'))
    result = {
      approvals: approvals.filter(
        (a) =>
          a.status === 'pending' &&
          (!parsed.searchParams.has('conversation_id') ||
            a.conversation_id === parsed.searchParams.get('conversation_id')),
      ),
    }
  else if (parsed.pathname.endsWith('/decision')) {
    const approval = approvals.find((a) => a.id === parts[parts.length - 2])
    if (!approval || approval.status !== 'pending' || approval.version !== body.version)
      throw new Error('审批已处理或版本已变化。')
    approval.status = body.decision === 'allow' ? 'consumed' : 'rejected'
    approval.note = String(body.note || '')
    approval.decided_at = now()
    approval.version++
    const task = tasks.find((t) => t.id === approval.task_id)
    emit(approval.conversation_id, approval.task_id, 'approval.decided', {
      ...approval,
      approval_id: approval.id,
      decision: body.decision,
      note: approval.note,
    })
    if (task) task.status = 'running'
    resumes.get(approval.id)?.(body.decision === 'allow', approval.note)
    resumes.delete(approval.id)
    result = { task_id: approval.task_id }
  } else if (parsed.pathname.endsWith('/cancel')) {
    const task = tasks.find((t) => t.id === parts[parts.length - 2])
    if (task) {
      task.status = 'cancelled'
      stopTimers(task.id)
      emit(task.conversation_id, task.id, 'task.cancelled', { message: '任务已停止。' })
      for (const a of approvals.filter((a) => a.task_id === task.id && a.status === 'pending')) {
        a.status = 'rejected'
        a.version++
        a.decided_at = now()
        emit(task.conversation_id, task.id, 'approval.decided', {
          ...a,
          approval_id: a.id,
          decision: 'deny',
        })
      }
    }
    result = task
  } else if (parsed.pathname.endsWith('/tasks'))
    result = {
      tasks: tasks.filter(
        (t) =>
          !parsed.searchParams.has('conversation_id') ||
          t.conversation_id === parsed.searchParams.get('conversation_id'),
      ),
    }
  else if (parsed.pathname.endsWith('/events/history'))
    result = {
      events: (events.get(parts[parts.length - 3]) || [])
        .filter((e) => e.sequence > Number(parsed.searchParams.get('after') || 0))
        .slice(0, 1000),
    }
  else if (parsed.pathname.endsWith('/snapshot')) {
    const sessionId = decodeURIComponent(parts[parts.length - 2])
    if (!conversations.some((item) => item.id === sessionId)) throw new Error('对话不存在。')
    const history = events.get(sessionId) || []
    result = {
      messages: messages.get(sessionId) || [],
      tasks: tasks.filter((item) => item.conversation_id === sessionId),
      approvals: approvals
        .filter((item) => item.conversation_id === sessionId && item.status === 'pending')
        .map(({ arguments: args, reuse_scope, ...approval }) => ({
          ...approval,
          arguments_json: JSON.stringify(args),
          reuse_scope_json: JSON.stringify(reuse_scope || {}),
        })),
      questions: [],
      last_sequence: history[history.length - 1]?.sequence || 0,
      truncated: false,
    }
  } else if (parsed.pathname.endsWith('/fork') && method === 'POST') {
    const sourceId = decodeURIComponent(parts[parts.length - 2])
    const key = new Headers(init?.headers).get('Idempotency-Key')
    if (!key) throw new Error('分岔请求缺少请求标识。')
    const identity = `${sourceId}:${key}`
    const messageId = String(body.message_id || '')
    const saved = forks.get(identity)
    if (saved) {
      if (saved.messageId !== messageId) throw new Error('同一请求标识不能用于不同消息。')
      result = saved.conversation
    } else {
      const source = conversations.find((item) => item.id === sourceId)
      if (!source) throw new Error('对话不存在。')
      if (
        tasks.some(
          (task) =>
            task.conversation_id === sourceId &&
            ['running', 'waiting_approval'].includes(task.status),
        )
      )
        throw new Error('任务尚未结束，暂时无法分岔。')
      const history = messages.get(sourceId) || []
      const index = messageId
        ? history.findIndex((message) => message.id === messageId)
        : history.length - 1
      const pivot = history[index]
      if (!pivot || pivot.role !== 'assistant') throw new Error('请从已保存的助手回复创建分岔。')
      const copied = history
        .slice(0, index + 1)
        .filter((message) => ['user', 'assistant'].includes(message.role))
      if (
        copied.length > 500 ||
        new TextEncoder().encode(copied.map((message) => message.content).join('')).length >
          2 * 1024 * 1024
      )
        throw new Error('此位置的对话历史过长，请选择较早的回复。')
      const branch: Conversation = {
        ...source,
        id: crypto.randomUUID(),
        title: source.title.slice(0, 190) + ' · 分岔',
        permission_mode: source.permission_mode === 'read_only' ? 'read_only' : 'workspace',
        archived: false,
        created_at: now(),
        updated_at: now(),
        message_count: copied.length,
        forked_from_conversation_id: sourceId,
        forked_from_message_id: pivot.id,
      }
      messages.set(
        branch.id,
        copied.map((message) => ({
          ...message,
          id: crypto.randomUUID(),
          conversation_id: branch.id,
        })),
      )
      conversations.unshift(branch)
      forks.set(identity, { messageId, conversation: branch })
      result = branch
    }
  } else if (parsed.pathname.endsWith('/messages')) {
    const sessionId = decodeURIComponent(parts[parts.length - 2])
    const conversation = conversations.find((item) => item.id === sessionId)
    if (!conversation) throw new Error('对话不存在。')
    if (method === 'POST') {
      const content = String(body.content)
      const key = new Headers(init?.headers).get('Idempotency-Key')
      const identity = `${sessionId}:${key}`
      const saved = key ? submissions.get(identity) : undefined
      if (saved) {
        if (saved.content !== content) throw new Error('同一请求标识不能用于不同消息。')
        const task = tasks.find((item) => item.id === saved.taskId)
        result = { task_id: saved.taskId, status: task?.status, replayed: true }
      } else {
        if (conversation.archived) throw new Error('对话已归档，恢复后可发送消息。')
        const receipt = start(sessionId, content)
        if (key) submissions.set(identity, { content, taskId: receipt.task_id })
        result = receipt
      }
    } else result = { messages: messages.get(sessionId) || [] }
  } else if (parsed.pathname.endsWith('/conversations')) {
    if (method === 'POST') {
      const conversation: Conversation = {
        ...conversationDefaults,
        workspace_id: 'personal',
        model_id: 'demo',
        permission_mode: 'workspace',
        response_mode: 'balanced',
        id: `session-${Date.now()}`,
        folder_id: String(body.folder_id || ''),
        archived: false,
        title: String(body.title || '新对话'),
        created_at: now(),
        updated_at: now(),
        message_count: 0,
      }
      conversations.unshift(conversation)
      result = conversation
    } else result = { conversations }
  } else if (parsed.pathname.includes('/conversations/')) {
    const conversation = conversations.find((c) => c.id === id)
    if (!conversation) throw new Error('对话不存在。')
    if (
      (method === 'DELETE' || body.archived !== undefined || body.folder_id !== undefined) &&
      tasks.some(
        (task) =>
          task.conversation_id === id && ['running', 'waiting_approval'].includes(task.status),
      )
    )
      throw new Error('任务尚未结束，暂时无法移动、归档或删除对话。')
    if (method === 'DELETE') {
      const owned = tasks.filter((task) => task.conversation_id === id)
      owned.forEach((task) => stopTimers(task.id))
      for (let index = tasks.length - 1; index >= 0; index--)
        if (tasks[index].conversation_id === id) tasks.splice(index, 1)
      for (let index = approvals.length - 1; index >= 0; index--)
        if (approvals[index].conversation_id === id) {
          resumes.delete(approvals[index].id)
          approvals.splice(index, 1)
        }
      conversations.splice(conversations.indexOf(conversation), 1)
      messages.delete(id)
      events.delete(id)
      for (const key of submissions.keys()) if (key.startsWith(`${id}:`)) submissions.delete(key)
    } else {
      Object.assign(conversation, body, method === 'PATCH' ? { updated_at: now() } : {})
      result = conversation
    }
  } else if (parsed.pathname.endsWith('/files'))
    result = {
      entries: ['README.md', 'notes'].map((name) => ({
        name,
        path: name,
        type: name.includes('.') ? 'file' : 'directory',
        modified_at: now(),
      })),
    }
  else if (parsed.pathname.endsWith('/files/content')) {
    const content = '# 个人手记\n想法与待办事项。'
    result = {
      path: parsed.searchParams.get('path') ?? 'README.md',
      content,
      binary: false,
      truncated: false,
      size: new TextEncoder().encode(content).byteLength,
      modified_at: now(),
      mime_type: 'text/markdown',
      data_url: '',
    } satisfies WorkspaceFileContent
  } else if (parsed.pathname.includes('/changes')) {
    const change = {
      path: 'notes/next-step.md',
      status: 'added',
      additions: 2,
      deletions: 0,
      diff: '+# 下一步\n+整理资料，列出待办事项。',
      diff_truncated: false,
    }
    result = parsed.pathname.endsWith('/diff')
      ? change
      : {
          changes: tasks.some((t) => t.status === 'succeeded') ? [change] : [],
          git: true,
          truncated: false,
        }
  } else throw new Error('此操作不可用。')
  return structuredClone(result) as T
}
