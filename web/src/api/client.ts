import { GatewayError, request, requestBlob, workspacePath } from './http'
import type { components } from './generated/transport'
import {
  validateConversationResponse,
  validateConversationsResponse,
  validateTaskResponse,
  validateTasksResponse,
  validateTaskResultResponse,
  validateApprovalsResponse,
  validateAuthorizationsResponse,
  validateMessagesResponse,
  validateEventsResponse,
  validateKnownEvent,
  validateEventEnvelope,
  validateSnapshotResponse,
  validateApprovalResponse,
  validateQuestionResponse,
  validateQuestionsResponse,
  validateWorkspaceFileResponse,
  validateModelCatalogResponse,
  validateModelBatchResponse,
} from './generated/validators.js'
import { mockConnect } from './mock'
import type { ContextUsage, PlanItem } from './events'
import { mapContextPayload } from './model-events'
import type { ImageReference } from './images'

type Transport = components['schemas']
export type TaskResult = Transport['TaskResultResponse']
export type KnownAgentEvent = Transport['KnownEvent']
export type PermissionMode =
  'read_only' | 'workspace' | 'workspace_auto' | 'danger_full_access' | 'full_access'
export type ApprovalScope = NonNullable<Transport['Decision']['scope']>
export type ApprovalAuthorization = Transport['AuthorizationResponse']
export type ResponseMode = 'fast' | 'balanced' | 'thorough'
export interface ThinkingControl {
  kind: 'effort' | 'toggle' | 'unavailable'
  options: string[]
  default: string
  source: string
  reason: string
}

export interface ToolSetting {
  name: string
  description: string
  source: 'builtin' | 'mcp'
  enabled: boolean
  configurable: boolean
  default_enabled: boolean
}

export interface ToolSettings {
  tools: ToolSetting[]
}

export interface Conversation {
  title_source?: 'pending' | 'automatic' | 'manual'
  memory_enabled?: boolean
  subagent_model_ids?: string[]
  max_subagent_calls?: number
  max_concurrent_subagents?: number
  folder_id?: string
  archived?: boolean
  id: string
  workspace_id: string
  title: string
  model_id: string
  permission_mode: PermissionMode
  response_mode: ResponseMode
  thinking?: string
  forked_from_conversation_id?: string
  forked_from_message_id?: string
  created_at: string
  updated_at: string
  message_count: number
}
export interface WorkspaceFolder {
  id: string
  name: string
  root_path: string
}

export interface WorkspaceFolderListing {
  path: string
  parent: string
  home: string
  root: string
  entries: { name: string; path: string }[]
  truncated: boolean
}

export interface ChatMessage {
  id: string
  conversation_id: string
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  created_at: string
  streaming?: boolean
  attachments_json?: string
}

export interface AgentEvent {
  schema_version: number
  event_id: string
  sequence: number
  type: string
  occurred_at?: string
  conversation_id: string
  task_id?: string
  payload: Record<string, unknown>
}

export interface Approval {
  id: string
  workspace_id: string
  conversation_id: string
  task_id: string
  tool_call_id: string
  tool_name: string
  arguments: Record<string, unknown>
  resource_scope: string
  risk_level: `L${1 | 2 | 3 | 4 | 5}`
  status: 'pending' | 'approved' | 'consumed' | 'rejected' | 'denied' | 'expired'
  version: number
  expires_at: string
  note?: string
  decided_at?: string
  allowed_scopes?: ApprovalScope[]
  reuse_scope?: Record<string, unknown>
  authorization_scope?: ApprovalScope
  authorization_expires_at?: string
  authorization_revoked_at?: string
}

export interface AgentQuestion {
  id: string
  workspace_id: string
  conversation_id: string
  task_id: string
  tool_call_id: string
  title: string
  options: { label: string; recommended?: boolean }[]
  status: 'pending' | 'answered' | 'cancelled'
  version: number
  answer?: string
  created_at: string
}

export type AgentActivityKind = 'reasoning' | 'tool' | 'subagent'
export type AgentActivityStatus = 'pending' | 'running' | 'completed' | 'failed' | 'denied'

export interface AgentActivityItem {
  id: string
  task_id: string
  kind: AgentActivityKind
  status: AgentActivityStatus
  title: string
  summary?: string
  detail?: unknown
  hint?: string
  action?: { label: string; to: string }
  sequence: number
  created_at: string
}

export interface AgentActivityGroup {
  task_id: string
  status: 'running' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled'
  items: AgentActivityItem[]
}

export interface PermissionSettings {
  default_mode: PermissionMode
}

export type SafetyRequestFormat = 'jev_like' | 'laya' | 'openai'

/**
 * `jev_like` wire layout: `message` puts the reviewed delta in `state.message`;
 * `inline` embeds it in the question instructions. Ignored by other formats.
 */
export type SafetyJevLayout = 'inline' | 'message'

/**
 * Prompt safety review settings (`GET/PATCH settings/safety`).
 * `api_key` is write-only and never returned; `credential_configured` is
 * read-only on write. This contract does not imply runtime enforcement.
 */
export interface SafetySettings {
  enabled: boolean
  request_format: SafetyRequestFormat
  jev_layout: SafetyJevLayout
  state: string
  decision_base_url: string
  decision_model: string
  credential_configured: boolean
  trusted_model_ids: string[]
  risk_threshold: number
  api_key?: string
}

export type SafetySettingsUpdate = Omit<SafetySettings, 'credential_configured'>

export interface AuditEvent {
  id: string
  action: string
  resource: string
  result: string
  details_json: string
  created_at: string
}

export type ChatModelProvider = 'openai' | 'anthropic' | 'openai_compatible' | 'ollama'
export interface DiscoveredChatModel {
  id: string
  name?: string
  owned_by?: string
  context_window?: number
  details?: string
}
export interface ChatModelSettings {
  provider: ChatModelProvider
  model: string
  baseUrl: string
  temperature: number
  maxTokens: number
  contextWindow?: number
  timeoutSeconds: number
  credentialConfigured: boolean
}

export interface ChatModelProfile extends ChatModelSettings {
  id: string
  name: string
  isDefault: boolean
}

export interface ModelCatalog {
  models: ChatModelProfile[]
  defaultModelId: string
  revision: string
}

export interface ModelBatchEdit {
  operationId: string
  expectedRevision: string
  profiles: ChatModelProfile[]
  removedIds: string[]
  apiKey: string
  reuseCredentials: boolean
}

interface ChatModelWire {
  id?: string
  name?: string
  provider: ChatModelProvider
  model: string
  base_url: string
  temperature: number
  max_tokens: number
  context_window?: number | null
  timeout_seconds: number
  credential_configured?: boolean
  is_default?: boolean
}

export interface WorkspaceFileEntry {
  name: string
  path: string
  type: 'file' | 'directory'
  size?: number
  modified_at: string
}

export type WorkspaceFileContent = Transport['WorkspaceFileResponse']

export interface WorkspaceChange {
  path: string
  status: 'modified' | 'added' | 'deleted' | 'renamed' | 'untracked'
  additions: number
  deletions: number
  diff: string
  diff_truncated: boolean
}

export interface CredentialStatus {
  name: string
  configured: boolean
  validated?: boolean
}

export interface CredentialEntry {
  service: string
  configured: boolean
  validated?: boolean
  updated_at?: string
}

export interface UserPreferences {
  assistantName: string
}

export interface RuntimeHealth {
  status: 'ok' | 'degraded'
  agentCore?: string
  version?: string
}

export interface RuntimeServiceStatus {
  status: 'serving' | 'unavailable' | string
  version?: string
}

export interface WorkspaceRuntimeStatus {
  workspaceId: string
  workspaceRoot: string
  workspaceSource: 'configuration' | 'environment' | 'selection'
  environment: string
  backend: RuntimeServiceStatus
  restart: {
    pending: boolean
    requiredFor: string[]
    hotApplied: string[]
    note: string
  }
}

export type MemoryCategory = 'preference' | 'fact' | 'constraint' | 'project' | 'other'

export interface UserMemory {
  folder_scope?: string | null
  id: string
  workspace_id: string
  subject_user_id: string
  category: MemoryCategory
  content: string
  source_type: string
  source_id: string
  confidence: number
  created_at: string
  updated_at: string
}

export interface MemoryInput {
  folder_id?: string
  category: MemoryCategory
  content: string
  confidence: number
}

export interface WorkspaceDataSummary {
  storage_backend: string
  conversation_count: number
  message_count: number
  memory_count: number
  task_count: number
  audit_event_count: number
}

export interface MCPServerStatus {
  namespace: string
  status: 'running' | 'disabled' | string
  transport: string
  tool_names: string[]
}

export interface MCPConfiguration {
  namespace: string
  transport: 'stdio'
  command: string
  args: string[]
  cwd: string
  enabled: boolean
  env_allowlist: string[]
  secret_env: string[]
  enabled_tools: string[] | null
  disabled_tools: string[]
  result_adapter?: 'none' | 'antenna_design'
  request_timeout_seconds?: number
}
export interface ManagedSkill {
  name: string
  content: string
  enabled: boolean
}
export interface ExtensionConfig {
  revision: number
  mcp_servers: MCPConfiguration[]
  skill_directories: string[]
  skills: ManagedSkill[]
  disabled_skills: string[]
}
export interface ExtensionSettings {
  config: ExtensionConfig
  active_config?: Pick<ExtensionConfig, 'revision' | 'mcp_servers'>
  extension_error?: string
  applied_revision: number
  reload_required: boolean
  reloading: boolean
  runtime_ready: boolean
  servers: MCPServerStatus[]
  skills: (InstalledSkill & { enabled: boolean })[]
  skill_errors: string[]
  management_available?: boolean
}

// View state also represents locally constructed turns before the server receipt.
export type AgentTask = Omit<
  Transport['TaskResponse'],
  'subagent_model_ids' | 'output_mode' | 'result_ready'
> &
  Partial<Pick<Transport['TaskResponse'], 'subagent_model_ids' | 'output_mode' | 'result_ready'>>

export interface InstalledSkill {
  name: string
  version: string
  description: string
  kind: 'instruction' | 'workflow'
  source_path: string
  parameters_schema: Record<string, unknown>
}

export interface ConversationSnapshot {
  messages: ChatMessage[]
  tasks: AgentTask[]
  approvals: Approval[]
  questions: AgentQuestion[]
  last_sequence: number
  truncated: boolean
}

export interface TaskInput {
  id: string
  task_id: string
  content: string
  status: 'queued' | 'consumed' | 'not_consumed'
  created_at: string
}

export interface TaskInspection {
  task: AgentTask
  approvals?: Approval[]
  projection: { plan: PlanItem[] | null; truncated: boolean }
  last_sequence: number
}

export interface ProcessJob {
  id: string
  task_id: string
  conversation_id: string
  command: string
  status: string
  log_tail: string
  live: boolean
  live_logs_available: boolean
  stdin_available: boolean
}

export interface MessageReceipt {
  task_id: string
  status: AgentTask['status'] | 'processing'
  replayed?: boolean
}

function chatModelFromWire(value: ChatModelWire): ChatModelSettings {
  return {
    provider: value.provider,
    model: value.model,
    baseUrl: value.base_url,
    temperature: value.temperature,
    maxTokens: value.max_tokens,
    contextWindow:
      Number.isSafeInteger(value.context_window) && (value.context_window ?? 0) > 0
        ? (value.context_window ?? undefined)
        : undefined,
    timeoutSeconds: value.timeout_seconds,
    credentialConfigured: value.credential_configured ?? false,
  }
}

function chatModelToWire(value: ChatModelSettings): Omit<ChatModelWire, 'credential_configured'> {
  return {
    provider: value.provider,
    model: value.model,
    base_url: value.baseUrl,
    temperature: value.temperature,
    max_tokens: value.maxTokens,
    context_window: value.contextWindow,
    timeout_seconds: value.timeoutSeconds,
  }
}

function chatModelProfileFromWire(value: ChatModelWire): ChatModelProfile {
  return {
    ...chatModelFromWire(value),
    id: value.id || '',
    name: value.name || value.model,
    isDefault: value.is_default ?? false,
  }
}

function chatModelProfileToWire(value: ChatModelProfile): Transport['ModelInput'] {
  return {
    ...chatModelToWire(value),
    id: value.id,
    name: value.name,
    is_default: value.isDefault,
    api_key: '',
    credential_source_id: '',
  }
}

function modelCatalogFromWire(value: Transport['ModelCatalogResponse']): ModelCatalog {
  return {
    models: value.models.map(chatModelProfileFromWire),
    defaultModelId: value.default_model_id,
    revision: value.revision,
  }
}

const path = workspacePath
function folderQuery(folderId = '') {
  return folderId ? `folder_id=${encodeURIComponent(folderId)}` : ''
}

export const api = {
  async getHealth(): Promise<RuntimeHealth> {
    const body = await request<{
      status: 'ok' | 'degraded'
      agent_core?: string
      version?: string
    }>('/health')
    return { status: body.status, agentCore: body.agent_core, version: body.version }
  },
  async getWorkspaceRuntimeStatus(folderId = ''): Promise<WorkspaceRuntimeStatus> {
    const body = await request<{
      workspace_id: string
      workspace_root: string
      workspace_source: 'configuration' | 'environment' | 'selection'
      environment: string
      backend: RuntimeServiceStatus
      restart: {
        pending: boolean
        required_for: string[]
        hot_applied: string[]
        note: string
      }
    }>(`${path}/runtime-status?${folderQuery(folderId)}`)
    return {
      workspaceId: body.workspace_id,
      workspaceRoot: body.workspace_root,
      workspaceSource: body.workspace_source,
      environment: body.environment,
      backend: body.backend,
      restart: {
        pending: body.restart.pending,
        requiredFor: body.restart.required_for || [],
        hotApplied: body.restart.hot_applied || [],
        note: body.restart.note,
      },
    }
  },
  async getUserPreferences(): Promise<UserPreferences> {
    const body = await request<{ assistant_name: string }>(`${path}/preferences`)
    return { assistantName: body.assistant_name }
  },
  async updateUserPreferences(preferences: UserPreferences): Promise<UserPreferences> {
    const body = await request<{ assistant_name: string }>(`${path}/preferences`, {
      method: 'PATCH',
      body: JSON.stringify({ assistant_name: preferences.assistantName }),
    })
    return { assistantName: body.assistant_name }
  },
  async getSettings(): Promise<{ chat: ChatModelSettings }> {
    const body = await request<{ models: { chat: ChatModelWire } }>(`${path}/settings`)
    return { chat: chatModelFromWire(body.models.chat) }
  },
  async updateChatModel(settings: ChatModelSettings): Promise<ChatModelSettings> {
    const body = await request<{ models: { chat: ChatModelWire } }>(`${path}/settings`, {
      method: 'PATCH',
      body: JSON.stringify({ models: { chat: chatModelToWire(settings) } }),
    })
    return chatModelFromWire(body.models.chat)
  },
  async listChatModels(): Promise<ModelCatalog> {
    return modelCatalogFromWire(
      await request(`${path}/models`, undefined, validateModelCatalogResponse),
    )
  },
  async updateChatModels(edit: ModelBatchEdit) {
    const input: Transport['ModelBatchInput'] = {
      operation_id: edit.operationId,
      expected_revision: edit.expectedRevision,
      profiles: edit.profiles.map(chatModelProfileToWire),
      removed_ids: edit.removedIds,
      api_key: edit.apiKey,
      reuse_credentials: edit.reuseCredentials,
    }
    const body = await request(
      `${path}/models/actions/batch`,
      {
        method: 'POST',
        body: JSON.stringify(input),
      },
      validateModelBatchResponse,
    )
    return {
      ...modelCatalogFromWire(body),
      savedIds: body.saved_ids,
      removedIds: body.removed_ids,
      error: body.error,
    }
  },
  getModelThinking(modelId: string, signal?: AbortSignal): Promise<ThinkingControl> {
    return request(`${path}/models/${encodeURIComponent(modelId)}/thinking`, { signal })
  },
  async getModelContext(modelId: string, signal?: AbortSignal): Promise<ContextUsage> {
    const body = await request<Record<string, unknown>>(
      `${path}/models/${encodeURIComponent(modelId)}/context`,
      { signal },
    )
    return mapContextPayload(body)
  },
  async saveChatModel(
    profile: ChatModelProfile,
    apiKey = '',
    credentialSourceId = '',
  ): Promise<ChatModelProfile> {
    const url = profile.id ? `${path}/models/${encodeURIComponent(profile.id)}` : `${path}/models`
    const body = await request<ChatModelWire>(url, {
      method: profile.id ? 'PUT' : 'POST',
      body: JSON.stringify({
        ...chatModelProfileToWire(profile),
        api_key: apiKey || undefined,
        credential_source_id: !apiKey && credentialSourceId ? credentialSourceId : undefined,
      }),
    })
    return chatModelProfileFromWire(body)
  },
  deleteChatModel(id: string): Promise<void> {
    return request(`${path}/models/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },
  async setDefaultChatModel(id: string): Promise<ModelCatalog> {
    const body = await request(
      `${path}/models/${encodeURIComponent(id)}/default`,
      { method: 'POST' },
      validateModelCatalogResponse,
    )
    return modelCatalogFromWire(body)
  },
  validateChatModel(
    settings: ChatModelSettings | ChatModelProfile,
    apiKey = '',
  ): Promise<{ ok: boolean; message: string; model: string }> {
    const profile = settings as Partial<ChatModelProfile>
    return request(`${path}/models/actions/validate`, {
      method: 'POST',
      body: JSON.stringify({
        chat: {
          ...chatModelToWire(settings),
          id: profile.id || undefined,
          name: profile.name || undefined,
        },
        api_key: apiKey,
      }),
    })
  },
  async discoverChatModels(
    settings: Pick<ChatModelSettings, 'provider' | 'baseUrl'> &
      Partial<Pick<ChatModelProfile, 'id'>>,
    apiKey = '',
    signal?: AbortSignal,
  ): Promise<DiscoveredChatModel[]> {
    const body = await request<{ models: DiscoveredChatModel[] }>(
      `${path}/models/actions/discover`,
      {
        method: 'POST',
        signal,
        body: JSON.stringify({
          id: settings.id || undefined,
          provider: settings.provider,
          base_url: settings.baseUrl,
          api_key: apiKey || undefined,
        }),
      },
    )
    return body.models || []
  },
  async listCredentialReferences(): Promise<CredentialStatus[]> {
    const body = await request<{ engines: CredentialStatus[] }>(`${path}/credential-references`)
    return body.engines || []
  },
  saveCredential(service: string, apiKey: string): Promise<void> {
    return request(`${path}/credential-references`, {
      method: 'POST',
      body: JSON.stringify({ service, api_key: apiKey }),
    })
  },
  deleteCredential(service: string, signal?: AbortSignal): Promise<void> {
    return request(`${path}/credential-references/${encodeURIComponent(service)}`, {
      method: 'DELETE',
      signal,
    })
  },
  async listMemories(query = '', folderId: string | null = null): Promise<UserMemory[]> {
    const params = new URLSearchParams()
    if (query.trim()) params.set('q', query.trim())
    if (folderId !== null) params.set('folder_id', folderId)
    const suffix = params.size ? `?${params}` : ''
    const body = await request<{ memories: UserMemory[] }>(`${path}/memories${suffix}`)
    return body.memories || []
  },
  getMemorySettings(folderId: string): Promise<{ folder_id: string; enabled: boolean }> {
    return request(`${path}/memory-settings?folder_id=${encodeURIComponent(folderId)}`)
  },
  setMemorySettings(
    folderId: string,
    enabled: boolean,
  ): Promise<{ folder_id: string; enabled: boolean }> {
    return request(`${path}/memory-settings`, {
      method: 'PATCH',
      body: JSON.stringify({ folder_id: folderId, enabled }),
    })
  },
  saveMemory(memory: MemoryInput & { id?: string }): Promise<UserMemory> {
    const url = memory.id ? `${path}/memories/${encodeURIComponent(memory.id)}` : `${path}/memories`
    return request(url, {
      method: memory.id ? 'PATCH' : 'POST',
      body: JSON.stringify({
        category: memory.category,
        content: memory.content,
        confidence: memory.confidence,
        folder_id: memory.folder_id,
      }),
    })
  },
  deleteMemory(id: string): Promise<void> {
    return request(`${path}/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },
  getWorkspaceDataSummary(): Promise<WorkspaceDataSummary> {
    return request(`${path}/data-summary`)
  },
  async listMCPServers(): Promise<MCPServerStatus[]> {
    const body = await request<{ servers: MCPServerStatus[] }>(`${path}/mcp-servers`)
    return body.servers || []
  },
  async listSkills(): Promise<InstalledSkill[]> {
    const body = await request<{ skills: InstalledSkill[] }>(`${path}/skills`)
    return body.skills || []
  },
  getExtensions(): Promise<ExtensionSettings> {
    return request(`${path}/extension-settings`)
  },
  saveExtensions(config: ExtensionConfig): Promise<ExtensionSettings> {
    return request(`${path}/extension-settings`, { method: 'PUT', body: JSON.stringify(config) })
  },
  applyExtensions(revision: number, restoreOnly = false): Promise<ExtensionSettings> {
    return request(`${path}/extension-settings/apply`, {
      method: 'POST',
      body: JSON.stringify({ revision, restore_only: restoreOnly }),
    })
  },
  async listTasks(conversationId?: string): Promise<AgentTask[]> {
    const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
    const body = await request(`${path}/tasks${query}`, undefined, validateTasksResponse)
    return body.tasks || []
  },
  submitTaskInput(taskId: string, content: string, key: string): Promise<TaskInput> {
    return request(`${path}/tasks/${encodeURIComponent(taskId)}/inputs`, {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify({ content }),
    })
  },
  getTaskResult(taskId: string, signal?: AbortSignal): Promise<TaskResult> {
    return request(
      `${path}/tasks/${encodeURIComponent(taskId)}/result`,
      { signal },
      validateTaskResultResponse,
    )
  },
  async listTaskInputs(taskId: string, signal?: AbortSignal): Promise<TaskInput[]> {
    const body = await request<{ inputs: TaskInput[] }>(
      `${path}/tasks/${encodeURIComponent(taskId)}/inputs`,
      { signal },
    )
    return body.inputs || []
  },
  getTaskInspection(taskId: string, signal?: AbortSignal): Promise<TaskInspection> {
    return request(`${path}/tasks/${encodeURIComponent(taskId)}/inspection`, { signal })
  },
  async listJobs(conversationId: string, signal?: AbortSignal): Promise<ProcessJob[]> {
    const body = await request<{ jobs: ProcessJob[] }>(
      `${path}/jobs?conversation_id=${encodeURIComponent(conversationId)}`,
      { signal },
    )
    return body.jobs || []
  },
  stopJob(jobId: string): Promise<void> {
    return request(`${path}/jobs/${encodeURIComponent(jobId)}/stop`, { method: 'POST' })
  },
  writeJobInput(jobId: string, text: string, eof = false): Promise<void> {
    return request(`${path}/jobs/${encodeURIComponent(jobId)}/input`, {
      method: 'POST',
      body: JSON.stringify({ text, eof }),
    })
  },
  async getCapabilities(signal?: AbortSignal): Promise<Record<string, boolean>> {
    const body = await request<{ capabilities: Record<string, boolean> }>(`${path}/capabilities`, {
      signal,
    })
    return body.capabilities || {}
  },
  resumeTask(taskId: string, version: number): Promise<MessageReceipt> {
    return request(`${path}/tasks/${encodeURIComponent(taskId)}/resume`, {
      method: 'POST',
      body: JSON.stringify({ version }),
    })
  },
  cancelTask(taskId: string): Promise<AgentTask> {
    return request(
      `${path}/tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: 'POST' },
      validateTaskResponse,
    )
  },
  async listTaskEvents(
    conversationId: string,
    after = 0,
    signal?: AbortSignal,
  ): Promise<AgentEvent[]> {
    const body = await request(
      `${path}/conversations/${encodeURIComponent(conversationId)}/events/history?after=${after}`,
      { signal },
      validateEventsResponse,
    )
    return body.events || []
  },
  async listConversations(): Promise<Conversation[]> {
    const body = await request(`${path}/conversations`, undefined, validateConversationsResponse)
    return body.conversations || []
  },
  async listWorkspaceFolders(): Promise<WorkspaceFolder[]> {
    const body = await request<{ folders?: WorkspaceFolder[] }>(`${path}/folders`)
    return (body.folders || []).map((folder) => ({ ...folder, id: folder.id || '' }))
  },
  browseWorkspaceFolders(
    location = '',
    showHidden = false,
    signal?: AbortSignal,
  ): Promise<WorkspaceFolderListing> {
    const query = new URLSearchParams({ path: location, show_hidden: String(showHidden) })
    return request(`${path}/folders/browse?${query}`, { signal })
  },
  async createWorkspaceFolder(rootPath: string, name = ''): Promise<WorkspaceFolder> {
    const folder = await request<WorkspaceFolder>(`${path}/folders`, {
      method: 'POST',
      body: JSON.stringify({ root_path: rootPath, name }),
    })
    return { ...folder, id: folder.id || '' }
  },
  deleteWorkspaceFolder(folderId: string): Promise<void> {
    return request(`${path}/folders/${encodeURIComponent(folderId)}`, { method: 'DELETE' })
  },
  getConversation(id: string): Promise<Conversation> {
    return request(
      `${path}/conversations/${encodeURIComponent(id)}`,
      undefined,
      validateConversationResponse,
    )
  },
  deleteConversation(id: string): Promise<void> {
    return request(`${path}/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },
  forkConversation(id: string, key: string, messageId = ''): Promise<Conversation> {
    return request(
      `${path}/conversations/${encodeURIComponent(id)}/fork`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: JSON.stringify({ message_id: messageId }),
      },
      validateConversationResponse,
    )
  },
  createConversation(data: {
    memory_enabled?: boolean
    subagent_model_ids?: string[]
    max_subagent_calls?: number
    max_concurrent_subagents?: number
    title?: string
    permission_mode?: PermissionMode
    model_id?: string
    response_mode?: ResponseMode
    thinking?: string
    folder_id?: string
  }): Promise<Conversation> {
    return request(
      `${path}/conversations`,
      { method: 'POST', body: JSON.stringify(data) },
      validateConversationResponse,
    )
  },
  updateConversationPermission(id: string, permissionMode: PermissionMode): Promise<Conversation> {
    return request(
      `${path}/conversations/${id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ permission_mode: permissionMode }),
      },
      validateConversationResponse,
    )
  },
  updateConversationSettings(
    id: string,
    settings: {
      memory_enabled?: boolean
      permission_mode?: PermissionMode
      model_id?: string
      response_mode?: ResponseMode
      thinking?: string
      title?: string
      folder_id?: string
      archived?: boolean
      max_subagent_calls?: number
      max_concurrent_subagents?: number
      subagent_model_ids?: string[]
    },
  ): Promise<Conversation> {
    return request(
      `${path}/conversations/${id}`,
      {
        method: 'PATCH',
        body: JSON.stringify(settings),
      },
      validateConversationResponse,
    )
  },
  async listMessages(id: string): Promise<ChatMessage[]> {
    const body = await request(
      `${path}/conversations/${id}/messages`,
      undefined,
      validateMessagesResponse,
    )
    return body.messages || []
  },
  async getConversationSnapshot(id: string): Promise<ConversationSnapshot> {
    const body = await request(
      `${path}/conversations/${encodeURIComponent(id)}/snapshot`,
      undefined,
      validateSnapshotResponse,
    )
    // The snapshot protocol stores these fields as JSON strings. Validate the
    // decoded values too; validating the outer string cannot check its shape.
    try {
      const questions = body.questions.map(({ options_json, ...question }) => {
        const options: unknown = JSON.parse(options_json)
        const decoded = { ...question, options }
        if (!validateQuestionResponse(decoded)) throw new Error('Invalid question')
        return decoded
      })
      const approvals = body.approvals.map(({ arguments_json, reuse_scope_json, ...approval }) => {
        const args: unknown = JSON.parse(arguments_json)
        const reuse_scope: unknown = JSON.parse(reuse_scope_json)
        const decoded = { ...approval, arguments: args, reuse_scope }
        if (!validateApprovalResponse(decoded)) throw new Error('Invalid approval')
        return decoded
      })
      return { ...body, questions, approvals }
    } catch {
      throw new GatewayError('对话快照格式异常，请更新应用或重试。', 502, 'CONTRACT_INVALID')
    }
  },
  sendMessage(
    id: string,
    content: string,
    idempotencyKey: string,
    attachments: ImageReference[] = [],
  ): Promise<MessageReceipt> {
    return request(`${path}/conversations/${id}/messages`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ content, attachments }),
    })
  },
  async listApprovals(conversationId?: string): Promise<Approval[]> {
    const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
    const body = await request(`${path}/approvals${query}`, undefined, validateApprovalsResponse)
    return body.approvals || []
  },
  async listQuestions(conversationId?: string): Promise<AgentQuestion[]> {
    const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
    const body = await request(`${path}/questions${query}`, undefined, validateQuestionsResponse)
    return body.questions || []
  },
  answerQuestion(id: string, answer: string, version: number): Promise<{ task_id: string }> {
    return request(`${path}/questions/${encodeURIComponent(id)}/answer`, {
      method: 'POST',
      body: JSON.stringify({ answer, version }),
    })
  },
  decideApproval(
    id: string,
    decision: 'allow' | 'deny',
    version: number,
    note?: string,
    scope: ApprovalScope = 'once',
  ): Promise<{ task_id: string }> {
    return request(`${path}/approvals/${encodeURIComponent(id)}/decision`, {
      method: 'POST',
      body: JSON.stringify({
        decision,
        version,
        note,
        scope: decision === 'deny' ? 'once' : scope,
      }),
    })
  },
  async listApprovalAuthorizations(
    id: string,
    signal?: AbortSignal,
  ): Promise<ApprovalAuthorization[]> {
    const body = await request(
      `${path}/approval-authorizations?conversation_id=${encodeURIComponent(id)}`,
      { signal },
      validateAuthorizationsResponse,
    )
    return body.authorizations || []
  },
  revokeApprovalAuthorization(id: string): Promise<void> {
    return request(`${path}/approvals/${encodeURIComponent(id)}/authorization`, {
      method: 'DELETE',
    })
  },
  getPermissionSettings(): Promise<PermissionSettings> {
    return request(`${path}/permissions`)
  },
  getToolSettings(): Promise<ToolSettings> {
    return request(`${path}/tool-settings`)
  },
  updateToolSettings(enabled: Record<string, boolean>): Promise<ToolSettings> {
    return request(`${path}/tool-settings`, { method: 'PATCH', body: JSON.stringify({ enabled }) })
  },
  updatePermissionSettings(settings: PermissionSettings): Promise<PermissionSettings> {
    return request(`${path}/permissions`, { method: 'PATCH', body: JSON.stringify(settings) })
  },
  getSafetySettings(signal?: AbortSignal): Promise<SafetySettings> {
    return request(`${path}/settings/safety`, { signal })
  },
  updateSafetySettings(
    settings: SafetySettingsUpdate,
    signal?: AbortSignal,
  ): Promise<SafetySettings> {
    return request(`${path}/settings/safety`, {
      method: 'PATCH',
      body: JSON.stringify(settings),
      signal,
    })
  },
  async listAuditEvents(): Promise<AuditEvent[]> {
    const body = await request<{ events: AuditEvent[] }>(`${path}/audit-events`)
    return body.events || []
  },
  async listWorkspaceFiles(relativePath = '', folderId = ''): Promise<WorkspaceFileEntry[]> {
    const body = await request<{ entries: WorkspaceFileEntry[] }>(
      `${path}/files?path=${encodeURIComponent(relativePath)}&${folderQuery(folderId)}`,
    )
    return body.entries || []
  },
  getWorkspaceFile(
    relativePath: string,
    folderId = '',
    signal?: AbortSignal,
  ): Promise<WorkspaceFileContent> {
    return request(
      `${path}/files/content?path=${encodeURIComponent(relativePath)}&${folderQuery(folderId)}`,
      { signal },
      validateWorkspaceFileResponse,
    )
  },
  downloadWorkspaceFile(relativePath: string, folderId = '', signal?: AbortSignal): Promise<Blob> {
    return requestBlob(
      `${path}/files/download?path=${encodeURIComponent(relativePath)}&${folderQuery(folderId)}`,
      { signal, cache: 'no-store' },
    )
  },
  createWorkspaceEntry(
    parent: string,
    name: string,
    type: 'file' | 'directory',
    folderId = '',
  ): Promise<WorkspaceFileEntry> {
    return request(`${path}/files?path=${encodeURIComponent(parent)}&${folderQuery(folderId)}`, {
      method: 'POST',
      body: JSON.stringify({ name, type }),
    })
  },
  openWorkspaceFolder(relativePath = '', folderId = ''): Promise<void> {
    return request(
      `${path}/files/open-folder?path=${encodeURIComponent(relativePath)}&${folderQuery(folderId)}`,
      { method: 'POST' },
    )
  },
  async listWorkspaceChanges(
    folderId = '',
    signal?: AbortSignal,
  ): Promise<{
    changes: WorkspaceChange[]
    git: boolean
    truncated: boolean
  }> {
    return request(`${path}/changes?${folderQuery(folderId)}`, { signal, cache: 'no-store' })
  },
  async getWorkspaceDiff(
    filePath: string,
    folderId = '',
    signal?: AbortSignal,
  ): Promise<WorkspaceChange> {
    return request(
      `${path}/changes/diff?path=${encodeURIComponent(filePath)}&${folderQuery(folderId)}`,
      { signal, cache: 'no-store' },
    )
  },
  connectEvents(conversationId: string, onEvent: (event: AgentEvent) => void): WebSocket {
    if (import.meta.env.VITE_MOCK === '1') return mockConnect(conversationId, onEvent)
    const configured = import.meta.env.VITE_WS_URL
    const host = location.protocol === 'file:' ? '127.0.0.1:8080' : location.host
    const base = configured || `${location.protocol === 'https:' ? 'wss' : 'ws'}://${host}`
    const socket = new WebSocket(
      `${base}${path}/events?conversation_id=${encodeURIComponent(conversationId)}`,
    )
    socket.onmessage = ({ data }) => {
      let event: unknown
      try {
        event = JSON.parse(String(data))
      } catch {
        // Reconnect/replay durable history instead of silently dropping a frame
        // which may have carried the terminal state.
        socket.close()
        return
      }
      // Projection failures belong to EventSocket's recovery boundary, not the
      // JSON parser. Swallowing them here can silently lose a terminal receipt.
      if (event && typeof event === 'object' && 'type' in event && event.type === 'stream.ready') {
        // Control frames are not durable task events and have no replay cursor.
        onEvent({
          type: 'stream.ready',
          schema_version: 1,
          event_id: '',
          sequence: 0,
          conversation_id: conversationId,
          task_id: '',
          payload: {},
        })
      } else if (validateKnownEvent(event) || validateEventEnvelope(event)) onEvent(event)
      else socket.close()
    }
    return socket
  },
}
