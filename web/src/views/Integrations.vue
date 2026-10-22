<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import Pulse from '@/components/shell/Pulse.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import SecretField from '@/components/common/SecretField.vue'
import InkSelect from '@/components/common/InkSelect.vue'
import { confirmAction } from '@/utils/confirm-action'
import {
  EXTENSION_BUNDLE_MAX_BYTES,
  mergeExtensionBundle,
  parseExtensionBundle,
  type ExtensionBundle,
} from '@/utils/extension-bundle'
import { useDialog } from '@/composables/useDialog'
import { api, type MCPConfiguration, type ManagedSkill } from '@/api/client'
import { useExtensions } from '@/composables/useExtensions'
import { t } from '@/i18n'

const { snapshot, busy, error, notice, perform, refresh, save, apply, restore } = useExtensions()
const tab = ref<'mcp' | 'skills'>('mcp')
const editable = computed(
  () =>
    !!snapshot.value &&
    snapshot.value.management_available !== false &&
    !busy.value &&
    !snapshot.value.reloading,
)
const server = ref<MCPConfiguration | null>(null)
const editingServer = ref(false)
const argumentsText = ref('')
const inheritedEnv = ref('')
const credentialNames = ref('')
const credentialName = ref('')
const credentialValue = ref('')
const credentialServer = ref('')
const skill = ref<ManagedSkill | null>(null)
const editingSkill = ref(false)
const directories = ref<string | null>(null)
const removed = ref<{ kind: 'mcp' | 'skill'; name: string } | null>(null)
const workflow = ref<ExtensionBundle | null>(null)
const replaceWorkflow = ref(false)
const changeWorkflowTools = ref(false)
const workflowServers = computed(() =>
  (workflow.value?.mcp_servers || []).map((incoming) => ({
    incoming,
    previous: snapshot.value?.config.mcp_servers.find(
      (item) => item.namespace === incoming.namespace,
    ),
  })),
)
const replacedWorkflowSkills = computed(() =>
  (workflow.value?.skills || []).filter((incoming) =>
    snapshot.value?.config.skills.some((item) => item.name === incoming.name),
  ),
)
const scannedWorkflowSkills = computed(() =>
  (workflow.value?.skills || []).filter((incoming) =>
    snapshot.value?.skills.some(
      (item) => item.name === incoming.name && !item.source_path.startsWith('managed:'),
    ),
  ),
)
const replacingWorkflow = computed(
  () =>
    workflowServers.value.some((item) => item.previous) || !!replacedWorkflowSkills.value.length,
)
const changedWorkflowTools = computed(() =>
  workflowServers.value.filter(
    ({ previous, incoming }) =>
      previous &&
      (JSON.stringify(previous.enabled_tools) !== JSON.stringify(incoming.enabled_tools) ||
        JSON.stringify(previous.disabled_tools) !== JSON.stringify(incoming.disabled_tools)),
  ),
)
const canSaveWorkflow = computed(
  () =>
    editable.value &&
    !!workflow.value &&
    !scannedWorkflowSkills.value.length &&
    (!replacingWorkflow.value || replaceWorkflow.value) &&
    (!changedWorkflowTools.value.length || changeWorkflowTools.value),
)
function workflowTools(tools: string[] | null) {
  return tools === null ? t('全部工具（包括今后新增）') : tools.join(', ') || t('无')
}
async function importWorkflow(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !editable.value) return
  feedbackTarget.value = 'workflow'
  workflow.value = null
  replaceWorkflow.value = changeWorkflowTools.value = false
  await perform(async () => {
    if (file.size > EXTENSION_BUNDLE_MAX_BYTES) throw new Error(t('工作流文件不能超过 1 MiB。'))
    try {
      workflow.value = parseExtensionBundle(await file.text())
    } catch (reason) {
      throw new Error(
        t(reason instanceof Error ? reason.message : '工作流文件格式无效或包含不支持的字段。'),
      )
    }
  })
}
async function saveWorkflow() {
  const incoming = workflow.value
  if (!incoming || !canSaveWorkflow.value) return
  if (
    await save((config) => {
      try {
        mergeExtensionBundle(config, incoming)
      } catch (reason) {
        throw new Error(
          t(reason instanceof Error ? reason.message : '工作流文件格式无效或包含不支持的字段。'),
        )
      }
    })
  ) {
    workflow.value = null
    notice.value = t('工作流已保存；尚未启动，请点击“应用并检查连接”。')
  }
}
watch(
  () => snapshot.value?.config.revision,
  () => {
    replaceWorkflow.value = changeWorkflowTools.value = false
  },
)
const splitLines = (value: string) =>
  value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
function setResultAdapter(value: string) {
  if (server.value && (value === 'none' || value === 'antenna_design'))
    server.value.result_adapter = value
}
function editServer(item?: MCPConfiguration) {
  server.value = item
    ? JSON.parse(JSON.stringify(item))
    : {
        namespace: '',
        transport: 'stdio',
        command: '',
        args: [],
        cwd: '',
        enabled: true,
        env_allowlist: [],
        secret_env: [],
        enabled_tools: null,
        disabled_tools: [],
        result_adapter: 'none',
        request_timeout_seconds: 30,
      }
  if (server.value) server.value.request_timeout_seconds ??= 30
  editingServer.value = !!item
  argumentsText.value = item?.args.join('\n') || ''
  inheritedEnv.value = item?.env_allowlist.join('\n') || ''
  credentialNames.value = item?.secret_env.join('\n') || ''
}
async function saveServer() {
  const current = server.value
  if (!current) return
  const item = {
    ...current,
    args: argumentsText.value.split('\n').filter((line) => line.length > 0),
    env_allowlist: splitLines(inheritedEnv.value),
    secret_env: splitLines(credentialNames.value),
  }
  if (
    await save((config) => {
      const index = config.mcp_servers.findIndex((row) => row.namespace === item.namespace)
      if (!editingServer.value && index >= 0) throw new Error(t('扩展名称已存在。'))
      if (index < 0) config.mcp_servers.push(item)
      else config.mcp_servers[index] = item
    })
  )
    server.value = null
}
function editSkill(item?: ManagedSkill) {
  editingSkill.value = !!item
  skill.value = item
    ? { ...item }
    : {
        name: 'my-workflow',
        enabled: true,
        content:
          '---\nname: my-workflow\ndescription: Describe the use case and deliverable\n---\nDefine steps, required tools, and stop conditions.\n',
      }
}
async function importSkill(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !skill.value) return
  await perform(async () => {
    if (file.size > 256 * 1024) throw new Error(t('技能文件不能超过 256 KiB。'))
    if (skill.value) skill.value.content = await file.text()
  })
  input.value = ''
}
async function saveSkill() {
  const item = skill.value
  if (!item) return
  if (
    await save((config) => {
      const index = config.skills.findIndex((row) => row.name === item.name)
      if (!editingSkill.value && index >= 0) throw new Error(t('扩展名称已存在。'))
      if (index < 0) config.skills.push({ ...item })
      else config.skills[index] = { ...item }
    })
  )
    skill.value = null
}
async function saveDirectories() {
  if (
    await save((config) => {
      config.skill_directories = splitLines(directories.value || '')
    })
  )
    directories.value = null
}
async function remove() {
  const target = removed.value
  if (!target) return
  if (
    await save((config) => {
      if (target.kind === 'mcp')
        config.mcp_servers = config.mcp_servers.filter((row) => row.namespace !== target.name)
      else config.skills = config.skills.filter((row) => row.name !== target.name)
    })
  ) {
    removed.value = null
    if (target.kind === 'mcp' && credentialServer.value === target.name) {
      credentialServer.value = credentialValue.value = ''
    }
  }
}
function toggleServer(namespace: string) {
  void save((config) => {
    const row = config.mcp_servers.find((item) => item.namespace === namespace)
    if (row) row.enabled = !row.enabled
  })
}
function toggleSkill(name: string, managed: boolean) {
  void save((config) => {
    if (managed) {
      const row = config.skills.find((item) => item.name === name)
      if (row) row.enabled = !row.enabled
    } else
      config.disabled_skills = config.disabled_skills.includes(name)
        ? config.disabled_skills.filter((item) => item !== name)
        : [...config.disabled_skills, name]
  })
}
function state(namespace: string) {
  const value = snapshot.value?.servers.find((item) => item.namespace === namespace)?.status
  const labels: Record<string, string> = {
    running: '运行中',
    disabled: '已停用',
    connecting: '正在连接',
    error: '连接失败，请检查命令和环境',
    needs_credentials: '需要凭证，请配置后连接',
    timeout: '连接超时',
    invalid_cwd: '工作目录不可用',
    disconnected: '连接已断开，请重新连接',
  }
  return t(labels[value || ''] || '尚未加载')
}
function openCredentials(namespace: string, names: string[]) {
  credentialServer.value = namespace
  credentialName.value = names[0] || ''
  credentialValue.value = ''
}
async function saveCredential(removeValue = false) {
  await perform(async () => {
    const reference = `mcp.${credentialServer.value}.${credentialName.value}`
    if (removeValue) await api.deleteCredential(reference)
    else await api.saveCredential(reference, credentialValue.value)
    credentialValue.value = ''
    notice.value = t(
      '凭证已更新。请结束任务并处理审批后，点击“应用并检查连接”使更改生效；“恢复已有连接”不会轮换健康连接的凭证。',
    )
  })
}
const directorySkills = computed(
  () => snapshot.value?.skills.filter((row) => !row.source_path.startsWith('managed:')) || [],
)
const selectedCredentialNames = computed(
  () =>
    snapshot.value?.config.mcp_servers.find((item) => item.namespace === credentialServer.value)
      ?.secret_env || [],
)

const removalDialog = ref<HTMLDialogElement | null>(null)
useDialog(removalDialog, () => !!removed.value)
const groups = computed(() => [
  {
    title: '已连接',
    items:
      snapshot.value?.config.mcp_servers.filter((item) =>
        snapshot.value?.servers.some(
          (row) => row.namespace === item.namespace && row.status === 'running',
        ),
      ) || [],
  },
  {
    title: '待连接或已停用',
    items:
      snapshot.value?.config.mcp_servers.filter(
        (item) =>
          !snapshot.value?.servers.some(
            (row) => row.namespace === item.namespace && row.status === 'running',
          ),
      ) || [],
  },
])
function pulse(namespace: string) {
  const status = snapshot.value?.servers.find((item) => item.namespace === namespace)?.status
  return status === 'running'
    ? 'idle'
    : status === 'connecting'
      ? 'thinking'
      : ['error', 'timeout', 'invalid_cwd'].includes(status || '')
        ? 'error'
        : status === 'needs_credentials'
          ? 'waiting'
          : 'offline'
}
const feedbackTarget = ref('')
function refreshPage() {
  feedbackTarget.value = ''
  void refresh()
}
function closeCredentials() {
  credentialServer.value = ''
  credentialValue.value = ''
}
async function clearCredential() {
  if (!editable.value) return
  const reference = `${credentialServer.value}.${credentialName.value}`
  if (
    (await confirmAction(t('清除凭证后，使用它的服务将无法连接。继续？'))) &&
    editable.value &&
    reference === `${credentialServer.value}.${credentialName.value}`
  )
    void scoped('credential', () => saveCredential(true))
}
async function scoped(name: string, action: () => void | Promise<unknown>) {
  feedbackTarget.value = name
  await action()
}
watch([server, skill, credentialServer], async () => {
  await nextTick()
  document
    .querySelector<HTMLElement>(
      '.extension-editor input:not(:disabled),.extension-editor textarea:not(:disabled)',
    )
    ?.focus()
})
</script>

<template>
  <main class="page-shell extensions-page">
    <header class="page-heading">
      <div>
        <h1 class="page-title">
          {{ $t('扩展') }}
        </h1>
      </div>
      <button
        class="secondary-button"
        :disabled="busy"
        @click="refreshPage"
      >
        {{ $t('刷新') }}
      </button>
    </header>
    <div
      v-if="busy && !snapshot"
      class="page-loading"
    >
      <InkLoader />
      <InkLoader />
      <InkLoader />
    </div>
    <section
      v-if="error && !feedbackTarget"
      class="page-state"
      role="alert"
    >
      <p>{{ error }}</p>
      <button
        class="secondary-button"
        :disabled="busy"
        @click="refresh"
      >
        {{ $t('重试') }}
      </button>
    </section>
    <template v-if="snapshot">
      <div class="extension-runtime">
        <Pulse
          :state="snapshot.reloading ? 'acting' : snapshot.runtime_ready ? 'idle' : 'offline'"
        />
        <span>
          {{
            snapshot.reloading
              ? $t('正在应用扩展')
              : snapshot.runtime_ready
                ? $t('连接已就绪')
                : $t('连接未就绪')
          }}
        </span>
        <button
          class="primary-button"
          :disabled="!editable"
          @click="scoped('runtime', apply)"
        >
          {{ $t('应用并检查连接') }}
        </button>
        <button
          v-if="snapshot.active_config"
          class="secondary-button"
          :disabled="!editable"
          @click="scoped('runtime', restore)"
        >
          {{ $t('恢复已有连接') }}
        </button>
      </div>
      <p class="page-copy">
        {{ $t('重连前请结束任务并处理审批。') }}
      </p>
      <p
        v-if="snapshot.management_available === false"
        class="page-copy"
      >
        {{ $t('扩展管理不可用，请检查服务连接。') }}
      </p>
      <p
        v-if="snapshot.reload_required"
        class="extension-wait"
      >
        {{ $t('有已保存但尚未应用的更改。') }}
      </p>
      <p
        v-if="snapshot.extension_error"
        class="extension-error"
        role="alert"
      >
        {{ snapshot.extension_error }}
        <button
          class="secondary-button"
          :disabled="busy"
          @click="refresh"
        >
          {{ $t('刷新') }}
        </button>
      </p>
      <p
        v-if="notice && (!feedbackTarget || feedbackTarget === 'runtime')"
        class="extension-result"
        role="status"
      >
        {{ notice }}
      </p>
      <p
        v-if="error && feedbackTarget === 'runtime'"
        class="extension-error"
        role="alert"
      >
        {{ error }}
      </p>
      <section aria-labelledby="workflow-import-title">
        <h2 id="workflow-import-title">
          {{ $t('导入工作流') }}
        </h2>
        <p class="page-copy">
          {{
            $t(
              '选择可信来源的工作流 JSON，同时导入 MCP 配置与 Skill。只保存配置，不安装依赖或启动程序。',
            )
          }}
        </p>
        <label>
          {{ $t('工作流 JSON（最多 1 MiB）') }}
          <input
            type="file"
            accept=".json,application/json"
            :disabled="!editable"
            @change="importWorkflow"
          />
        </label>
        <form
          v-if="workflow"
          class="extension-editor"
          @submit.prevent="scoped('workflow', saveWorkflow)"
        >
          <fieldset :disabled="!editable">
            <h3>{{ $t('工作流预览：{0}', [workflow.name]) }}</h3>
            <p class="page-copy">
              {{
                $t(
                  '请核对命令、路径和技能全文。应用后程序可在本机运行；不相关配置、扫描目录和全局工具开关保持不变。',
                )
              }}
            </p>
            <article
              v-for="{ incoming, previous } in workflowServers"
              :key="incoming.namespace"
              class="extension-item"
            >
              <h3>MCP · {{ incoming.namespace }}</h3>
              <p>{{ previous ? $t('将替换同名服务') : $t('将添加服务') }}</p>
              <p>{{ incoming.enabled ? $t('配置：启用') : $t('配置：停用') }}</p>
              <label>
                {{ $t('可执行程序') }}
                <input
                  :value="incoming.command"
                  readonly
                />
              </label>
              <label>
                {{ $t('启动参数（JSON 数组，逐项传入，不经 shell）') }}
                <textarea
                  :value="JSON.stringify(incoming.args, null, 2)"
                  rows="5"
                  readonly
                />
              </label>
              <p>
                {{ $t('工作目录（留空使用默认工作区）') }}: {{ incoming.cwd || $t('默认工作区') }}
              </p>
              <p>{{ $t('工具请求超时（秒）') }}: {{ incoming.request_timeout_seconds }}</p>
              <p>
                {{ $t('结果展示') }}:
                {{
                  incoming.result_adapter === 'antenna_design'
                    ? $t('天线结果与方向图')
                    : $t('标准工具结果')
                }}
              </p>
              <p>{{ $t('继承环境变量名') }}: {{ workflowTools(incoming.env_allowlist) }}</p>
              <p>{{ $t('凭证环境变量名') }}: {{ workflowTools(incoming.secret_env) }}</p>
              <p class="extension-tools">
                {{ $t('导入后允许工具') }}: {{ workflowTools(incoming.enabled_tools) }}
              </p>
              <p class="extension-tools">
                {{ $t('导入后禁用工具') }}: {{ workflowTools(incoming.disabled_tools) }}
              </p>
              <template v-if="previous">
                <p class="extension-tools">
                  {{ $t('当前允许工具') }}: {{ workflowTools(previous.enabled_tools) }}
                </p>
                <p class="extension-tools">
                  {{ $t('当前禁用工具') }}: {{ workflowTools(previous.disabled_tools) }}
                </p>
              </template>
            </article>
            <article
              v-for="incoming in workflow.skills"
              :key="incoming.name"
              class="extension-item"
            >
              <h3>Skill · {{ incoming.name }}</h3>
              <p>
                {{
                  replacedWorkflowSkills.some((item) => item.name === incoming.name)
                    ? $t('将替换已导入的同名技能副本')
                    : $t('将添加技能')
                }}
              </p>
              <p>{{ incoming.enabled ? $t('配置：启用') : $t('配置：停用') }}</p>
              <details>
                <summary>{{ $t('查看技能全文') }}</summary>
                <textarea
                  :value="incoming.content"
                  :aria-label="$t('技能全文：{0}', [incoming.name])"
                  rows="10"
                  readonly
                />
              </details>
            </article>
            <p
              v-if="scannedWorkflowSkills.length"
              class="extension-error"
              role="alert"
            >
              {{
                $t(
                  '扫描目录中已有同名技能：{0}。不能覆盖；请先移除目录中的重复项或调整扫描目录，应用后重新导入。',
                  [scannedWorkflowSkills.map((item) => item.name).join(', ')],
                )
              }}
            </p>
            <label
              v-if="replacingWorkflow"
              class="check"
            >
              <input
                v-model="replaceWorkflow"
                type="checkbox"
              />
              {{ $t('我确认替换上述同名服务配置和技能副本。') }}
            </label>
            <template v-if="changedWorkflowTools.length">
              <p class="extension-wait">
                {{
                  $t(
                    '这些服务的工具范围将改变：{0}。新增工具需在允许列表中，且不在禁用列表中；全局停用的工具仍需手动启用。',
                    [changedWorkflowTools.map((item) => item.incoming.namespace).join(', ')],
                  )
                }}
              </p>
              <label class="check">
                <input
                  v-model="changeWorkflowTools"
                  type="checkbox"
                />
                {{ $t('我已核对前后差异，同意更新这些服务的允许/禁用工具列表。') }}
              </label>
            </template>
            <p class="page-copy">
              {{
                $t(
                  '若更新了正在运行的服务源码，请先完成任务并重启应用后端，再应用配置；仅重新发现工具不会重启健康进程。',
                )
              }}
            </p>
            <div class="page-actions">
              <button
                class="secondary-button"
                type="button"
                @click="workflow = null"
              >
                {{ $t('取消') }}
              </button>
              <button
                class="primary-button"
                type="submit"
                :disabled="!canSaveWorkflow"
              >
                {{ $t('确认导入并保存') }}
              </button>
            </div>
          </fieldset>
        </form>
        <p
          v-if="error && feedbackTarget === 'workflow'"
          class="extension-error"
          role="alert"
        >
          {{ error }}
        </p>
        <p
          v-if="notice && feedbackTarget === 'workflow'"
          class="extension-result"
          role="status"
        >
          {{ notice }}
        </p>
      </section>
      <nav
        class="extension-tabs"
        :aria-label="$t('扩展分类')"
      >
        <button
          :aria-pressed="tab === 'mcp'"
          @click="tab = 'mcp'"
        >
          MCP
        </button>
        <button
          :aria-pressed="tab === 'skills'"
          @click="tab = 'skills'"
        >
          Skill
        </button>
      </nav>
      <section v-if="tab === 'mcp'">
        <header class="page-heading">
          <h2>{{ $t('外部工具服务') }}</h2>
          <button
            class="secondary-button"
            :disabled="!editable"
            @click="editServer()"
          >
            {{ $t('添加 MCP') }}
          </button>
        </header>
        <p class="page-copy">
          {{ $t('添加的程序会在本机运行，请只使用可信来源。') }}
        </p>
        <form
          v-if="server"
          class="extension-editor"
          @submit.prevent="scoped(server.namespace, saveServer)"
        >
          <fieldset :disabled="!editable">
            <label>
              {{ $t('名称标识') }}
              <input
                v-model="server.namespace"
                required
                pattern="[a-z][a-z0-9_-]{0,47}"
                :disabled="editingServer"
                placeholder="my-tools"
              />
            </label>
            <label>
              {{ $t('可执行程序') }}
              <input
                v-model="server.command"
                required
                placeholder="uvx / npx / python"
              />
            </label>
            <label>
              {{ $t('启动参数，每行一个') }}
              <textarea
                v-model="argumentsText"
                rows="3"
              />
            </label>
            <label>
              {{ $t('工作目录（留空使用默认工作区）') }}
              <input v-model="server.cwd" />
            </label>
            <label>
              {{ $t('工具请求超时（秒）') }}
              <input
                v-model.number="server.request_timeout_seconds"
                type="number"
                min="1"
                max="660"
                step="1"
                required
              />
            </label>
            <label>
              {{ $t('结果展示') }}
              <InkSelect
                :model-value="server.result_adapter || 'none'"
                :options="[
                  { value: 'none', label: $t('标准工具结果') },
                  { value: 'antenna_design', label: $t('天线结果与方向图') },
                ]"
                @update:model-value="setResultAdapter"
              />
            </label>
            <p
              v-if="server.result_adapter === 'antenna_design'"
              class="page-copy"
            >
              {{
                $t(
                  '批准天线计算后，将校验结果文件并保存到当前对话工作区，显示方向图。只用于可信的天线服务。',
                )
              }}
            </p>
            <label>
              {{ $t('继承的环境变量名，每行一个') }}
              <textarea
                v-model="inheritedEnv"
                rows="2"
              />
            </label>
            <label>
              {{ $t('凭证环境变量名，每行一个') }}
              <textarea
                v-model="credentialNames"
                rows="2"
                placeholder="API_KEY"
              />
            </label>
            <label class="check">
              <input
                v-model="server.enabled"
                type="checkbox"
              />
              {{ $t('启用') }}
            </label>
            <div class="page-actions">
              <button
                class="primary-button"
                type="submit"
              >
                {{ $t('保存配置') }}
              </button>
              <button
                class="secondary-button"
                type="button"
                @click="server = null"
              >
                {{ $t('取消') }}
              </button>
            </div>
            <p
              v-if="error && feedbackTarget === server.namespace"
              class="extension-error"
              role="alert"
            >
              {{ error }}
            </p>
          </fieldset>
        </form>
        <section
          v-if="!snapshot.config.mcp_servers.length"
          class="page-state"
        >
          <Pulse state="offline" />
          <strong>{{ $t('还没有连接外部工具') }}</strong>
          <p>{{ $t('添加可信的本地服务，工具会在连接成功后可用。') }}</p>
          <button
            class="secondary-button"
            :disabled="!editable"
            @click="editServer()"
          >
            {{ $t('添加 MCP') }}
          </button>
        </section>
        <section
          v-for="group in groups"
          :key="group.title"
          class="extension-group"
        >
          <h3 v-if="group.items.length">
            {{ $t(group.title) }}
          </h3>
          <article
            v-for="item in group.items"
            :key="item.namespace"
            class="extension-item"
          >
            <header>
              <Pulse :state="pulse(item.namespace)" />
              <strong>{{ item.namespace }}</strong>
              <span>{{ state(item.namespace) }}</span>
            </header>
            <p class="page-copy">
              <code>{{ item.command }}</code>
              ·
              {{ item.enabled ? $t('配置：启用') : $t('配置：停用') }}
            </p>
            <p
              v-if="
                snapshot.servers.find((row) => row.namespace === item.namespace)?.tool_names.length
              "
              class="extension-tools"
            >
              {{
                snapshot.servers
                  .find((row) => row.namespace === item.namespace)
                  ?.tool_names.join(' · ')
              }}
            </p>
            <div class="page-actions">
              <button
                class="secondary-button"
                :disabled="!editable"
                @click="editServer(item)"
              >
                {{ $t('编辑') }}
              </button>
              <button
                class="secondary-button"
                :disabled="!editable"
                @click="scoped(item.namespace, () => toggleServer(item.namespace))"
              >
                {{ item.enabled ? $t('停用') : $t('启用') }}
              </button>
              <button
                v-if="item.secret_env.length"
                class="secondary-button"
                :disabled="!editable"
                @click="openCredentials(item.namespace, item.secret_env)"
              >
                {{ $t('配置凭证') }}
              </button>
              <button
                v-if="pulse(item.namespace) === 'error' || pulse(item.namespace) === 'offline'"
                class="secondary-button"
                :disabled="!editable"
                @click="scoped(item.namespace, apply)"
              >
                {{ $t('重新检查连接') }}
              </button>
              <button
                class="danger-button"
                :disabled="!editable"
                @click="removed = { kind: 'mcp', name: item.namespace }"
              >
                {{ $t('移除') }}
              </button>
            </div>
            <p
              v-if="feedbackTarget === item.namespace && error"
              class="extension-error"
              role="alert"
            >
              {{ error }}
              <button
                class="secondary-button"
                :disabled="!editable"
                @click="scoped(item.namespace, refresh)"
              >
                {{ $t('刷新') }}
              </button>
            </p>
            <p
              v-else-if="feedbackTarget === item.namespace && notice"
              class="extension-result"
              role="status"
            >
              {{ notice }}
            </p>
          </article>
        </section>
        <form
          v-if="credentialServer"
          class="extension-editor"
          @submit.prevent="scoped('credential', () => saveCredential())"
        >
          <fieldset :disabled="!editable">
            <h3>{{ $t('配置凭证') }} · {{ credentialServer }}</h3>
            <label>
              {{ $t('环境变量名') }}
              <InkSelect
                v-model="credentialName"
                :disabled="!editable"
                :options="selectedCredentialNames.map((name) => ({ value: name, label: name }))"
              />
            </label>
            <label>
              {{ $t('新的凭证值') }}
              <SecretField
                v-model="credentialValue"
                :label="$t('新的凭证值')"
                :disabled="!editable"
              />
            </label>
            <div class="page-actions">
              <button
                class="primary-button"
                type="submit"
                :disabled="!credentialValue || !credentialName"
              >
                {{ $t('保存凭证') }}
              </button>
              <button
                class="danger-button"
                type="button"
                :disabled="!credentialName"
                @click="clearCredential"
              >
                {{ $t('清除凭证') }}
              </button>
              <button
                class="secondary-button"
                type="button"
                @click="closeCredentials"
              >
                {{ $t('关闭') }}
              </button>
            </div>
            <p
              v-if="feedbackTarget === 'credential' && error"
              role="alert"
              class="extension-error"
            >
              {{ error }}
            </p>
            <p
              v-else-if="feedbackTarget === 'credential' && notice"
              role="status"
              class="extension-result"
            >
              {{ notice }}
            </p>
          </fieldset>
        </form>
      </section>
      <section v-else>
        <header class="page-heading">
          <h2>{{ $t('个人技能') }}</h2>
          <div class="page-actions">
            <button
              class="secondary-button"
              :disabled="!editable"
              @click="editSkill()"
            >
              {{ $t('添加或导入技能') }}
            </button>
            <button
              class="secondary-button"
              :disabled="!editable"
              @click="directories = snapshot.config.skill_directories.join('\n')"
            >
              {{ $t('扫描目录') }}
            </button>
          </div>
        </header>
        <form
          v-if="directories !== null"
          class="extension-editor"
          @submit.prevent="scoped('directories', saveDirectories)"
        >
          <fieldset :disabled="!editable">
            <label>
              {{ $t('技能目录，每行一个') }}
              <textarea
                v-model="directories"
                rows="3"
              />
            </label>
            <div class="page-actions">
              <button
                type="submit"
                class="primary-button"
              >
                {{ $t('保存配置') }}
              </button>
              <button
                type="button"
                class="secondary-button"
                @click="directories = null"
              >
                {{ $t('取消') }}
              </button>
            </div>
            <p
              v-if="feedbackTarget === 'directories' && error"
              class="extension-error"
              role="alert"
            >
              {{ error }}
            </p>
            <p
              v-else-if="feedbackTarget === 'directories' && notice"
              class="extension-result"
              role="status"
            >
              {{ notice }}
            </p>
          </fieldset>
        </form>
        <form
          v-if="skill"
          class="extension-editor"
          @submit.prevent="scoped(skill.name, saveSkill)"
        >
          <fieldset :disabled="!editable">
            <label>
              {{ $t('名称标识（与 SKILL.md 的 name 一致）') }}
              <input
                v-model="skill.name"
                required
                :disabled="editingSkill"
                pattern="[a-z0-9][a-z0-9._-]{0,63}"
              />
            </label>
            <label>
              {{ $t('导入文件') }}
              <input
                type="file"
                accept=".md,text/markdown,text/plain"
                @change="importSkill"
              />
            </label>
            <label>
              SKILL.md
              <textarea
                v-model="skill.content"
                rows="12"
                required
                spellcheck="false"
              />
            </label>
            <label class="check">
              <input
                v-model="skill.enabled"
                type="checkbox"
              />
              {{ $t('启用') }}
            </label>
            <div class="page-actions">
              <button
                type="submit"
                class="primary-button"
              >
                {{ $t('保存技能') }}
              </button>
              <button
                type="button"
                class="secondary-button"
                @click="skill = null"
              >
                {{ $t('取消') }}
              </button>
            </div>
            <p
              v-if="error && feedbackTarget === skill.name"
              class="extension-error"
              role="alert"
            >
              {{ error }}
            </p>
          </fieldset>
        </form>
        <h3 v-if="snapshot.config.skills.length">
          {{ $t('已添加技能') }}
        </h3>
        <article
          v-for="item in snapshot.config.skills"
          :key="item.name"
          class="extension-item"
        >
          <header>
            <Pulse
              :state="
                snapshot.skills.some(
                  (row) =>
                    row.name === item.name && row.source_path.startsWith('managed:') && row.enabled,
                )
                  ? 'idle'
                  : 'offline'
              "
            />
            <strong>{{ item.name }}</strong>
            <span>
              {{
                snapshot.skills.some(
                  (row) =>
                    row.name === item.name && row.source_path.startsWith('managed:') && row.enabled,
                )
                  ? $t('已加载')
                  : $t('未加载或已停用')
              }}
            </span>
          </header>
          <div class="page-actions">
            <button
              class="secondary-button"
              :disabled="!editable"
              @click="editSkill(item)"
            >
              {{ $t('查看与编辑') }}
            </button>
            <button
              class="secondary-button"
              :disabled="!editable"
              @click="scoped(item.name, () => toggleSkill(item.name, true))"
            >
              {{ item.enabled ? $t('停用') : $t('启用') }}
            </button>
            <button
              class="danger-button"
              :disabled="!editable"
              @click="removed = { kind: 'skill', name: item.name }"
            >
              {{ $t('移除') }}
            </button>
          </div>
          <p
            v-if="feedbackTarget === item.name && error"
            class="extension-error"
            role="alert"
          >
            {{ error }}
            <button
              class="secondary-button"
              :disabled="!editable"
              @click="scoped(item.name, () => toggleSkill(item.name, true))"
            >
              {{ $t('重试') }}
            </button>
          </p>
          <p
            v-else-if="feedbackTarget === item.name && notice"
            class="extension-result"
            role="status"
          >
            {{ notice }}
          </p>
        </article>
        <h3 v-if="directorySkills.length">
          {{ $t('目录技能') }}
        </h3>
        <article
          v-for="item in directorySkills"
          :key="item.source_path"
          class="extension-item"
        >
          <header>
            <Pulse :state="item.enabled ? 'idle' : 'offline'" />
            <strong>{{ item.name }}</strong>
          </header>
          <p>{{ item.description }}</p>
          <p class="extension-tools">
            {{ item.source_path }}
          </p>
          <button
            class="secondary-button"
            :disabled="!editable"
            @click="scoped(item.name, () => toggleSkill(item.name, false))"
          >
            {{ snapshot.config.disabled_skills.includes(item.name) ? $t('启用') : $t('停用') }}
          </button>
          <p
            v-if="feedbackTarget === item.name && error"
            class="extension-error"
            role="alert"
          >
            {{ error }}
            <button
              class="secondary-button"
              :disabled="!editable"
              @click="scoped(item.name, () => toggleSkill(item.name, false))"
            >
              {{ $t('重试') }}
            </button>
          </p>
          <p
            v-else-if="feedbackTarget === item.name && notice"
            class="extension-result"
            role="status"
          >
            {{ notice }}
          </p>
        </article>
        <section
          v-if="!snapshot.skills.length && !snapshot.config.skills.length"
          class="page-state"
        >
          <strong>{{ $t('还没有可用技能') }}</strong>
          <p>{{ $t('导入自己的 SKILL.md，或选择已有技能目录。') }}</p>
          <button
            class="secondary-button"
            :disabled="!editable"
            @click="editSkill()"
          >
            {{ $t('添加或导入技能') }}
          </button>
        </section>
        <p
          v-for="message in snapshot.skill_errors"
          :key="message"
          class="extension-error"
          role="alert"
        >
          {{ message }}
        </p>
      </section>
    </template>
    <dialog
      ref="removalDialog"
      class="extension-confirm"
      aria-labelledby="extension-remove-title"
      @cancel="removed = null"
      @close="removed = null"
    >
      <template v-if="removed">
        <h2 id="extension-remove-title">
          {{ $t('移除扩展') }}
        </h2>
        <p>
          {{ $t('移除扩展 {0}？应用更改后停用，原文件和已保存的凭证会保留。', [removed.name]) }}
        </p>
        <div class="page-actions">
          <button
            class="secondary-button"
            @click="removed = null"
          >
            {{ $t('取消') }}
          </button>
          <button
            class="danger-button"
            :disabled="!editable"
            @click="remove"
          >
            {{ $t('确认移除') }}
          </button>
        </div>
        <p
          v-if="error"
          role="alert"
        >
          {{ error }}
        </p>
      </template>
    </dialog>
  </main>
</template>
<style scoped lang="scss">
@use '@/styles/integrations';
</style>
