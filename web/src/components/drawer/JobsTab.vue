<script setup lang="ts">
import { t } from '@/i18n'
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, type AgentTask, type ProcessJob } from '@/api/client'
import { useRunStore } from '@/stores/run'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import RunEntry from './RunEntry.vue'
import RunWorkspace from './RunWorkspace.vue'
const run = useRunStore(),
  session = useSessionStore(),
  ui = useUIStore()
const tasks = ref<AgentTask[]>([]),
  jobs = ref<ProcessJob[]>([]),
  error = ref(''),
  loading = ref(false)
const liveStatuses = ['created', 'queued', 'running', 'waiting_approval', 'retry_wait']
const activeTasks = computed(() => tasks.value.filter((task) => liveStatuses.includes(task.status)))
const history = computed(() =>
  tasks.value
    .filter((task) => !liveStatuses.includes(task.status))
    .slice()
    .reverse(),
)
const turns = computed(() => run.turns[session.activeSessionId || ''] || [])
const turnById = computed(() => new Map(turns.value.map((turn) => [turn.id, turn])))
const pages = ref<InstanceType<typeof RunWorkspace> | null>(null)
const historyLimit = ref(20)
function openSubagent(turnId: string, childId: string) {
  pages.value?.open(turnId, childId)
}
watch(
  () => session.activeSessionId,
  () => {
    historyLimit.value = 20
  },
)
const visible = computed(() => ui.drawerOpen && ui.drawerTab === 'jobs')
const stoppingJobs = ref(new Set<string>())
const actionError = ref('')
const sendingInput = ref(new Set<string>())
const jobInputs = ref<Record<string, string>>({})
const labels: Record<AgentTask['status'], string> = {
  created: '待开始',
  queued: '排队中',
  running: '正在做',
  waiting_approval: '等你处理',
  retry_wait: '等待重试',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已停止',
  rejected: '已拒绝',
  expired: '已过期',
  timed_out: '超时',
  interrupted: '已中断',
}
let controller: AbortController | undefined
let generation = 0
let reload = false
let timer: ReturnType<typeof setTimeout> | undefined
function cleanup() {
  generation++
  controller?.abort()
  controller = undefined
  if (timer) clearTimeout(timer)
  timer = undefined
  loading.value = false
  reload = false
  jobInputs.value = {}
}
async function load() {
  const id = session.activeSessionId
  if (!id || !visible.value) return
  if (loading.value) {
    reload = true
    return
  }
  const current = generation
  controller = new AbortController()
  loading.value = true
  try {
    const [items, processes] = await Promise.all([
      api.listTasks(id),
      session.capabilities.process_list ? api.listJobs(id, controller.signal) : Promise.resolve([]),
    ])
    if (current !== generation) return
    tasks.value = items
    jobs.value = processes
    const interactive = new Set(
      processes.filter((job) => job.live && job.stdin_available).map((job) => job.id),
    )
    for (const key of Object.keys(jobInputs.value))
      if (!interactive.has(key)) delete jobInputs.value[key]
    error.value = ''
  } catch (reason) {
    if (current === generation)
      error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (current === generation) {
      loading.value = false
      if (timer) clearTimeout(timer)
      if (reload && visible.value) {
        reload = false
        void load()
      } else {
        // Only an open panel with real live processes needs log-tail polling.
        if (visible.value && jobs.value.some((job) => job.live))
          timer = setTimeout(() => {
            void load()
          }, 2000)
      }
    }
  }
}
function operation(task: AgentTask) {
  const turn = turnById.value.get(task.id)
  if (task.status === 'waiting_approval') {
    const pending = turn?.awaits.find(
      (item) => !item.closed && !item.verdict && item.answer === undefined,
    )
    return pending?.mode === 'question'
      ? pending.title || t('Agent 在等你回答')
      : t('等你确认后继续…')
  }
  if (turn?.status === 'running' && turn.compaction?.status === 'running')
    return t('正在压缩上下文')
  return (
    turn?.steps
      .slice()
      .reverse()
      .find((step) => step.status === 'running')?.title || t(labels[task.status])
  )
}
async function stop(task: AgentTask) {
  if (await session.cancelTask(task.id, task.conversation_id)) await load()
  else error.value = session.error || t('未能确认停止，请稍后重试。')
}
async function stopJob(job: ProcessJob) {
  if (stoppingJobs.value.has(job.id)) return
  const current = generation
  stoppingJobs.value.add(job.id)
  actionError.value = ''
  try {
    await api.stopJob(job.id)
    if (current === generation) await load()
  } catch (reason) {
    if (current === generation)
      actionError.value = reason instanceof Error ? reason.message : t('未能确认停止，请稍后重试。')
  } finally {
    stoppingJobs.value.delete(job.id)
  }
}
async function writeInput(job: ProcessJob, eof = false) {
  if (sendingInput.value.has(job.id)) return
  const text = eof ? '' : `${jobInputs.value[job.id] || ''}\n`
  if (new TextEncoder().encode(text).length > 4096) {
    actionError.value = t('单次进程输入不能超过 4096 字节。')
    return
  }
  const current = generation
  sendingInput.value.add(job.id)
  actionError.value = ''
  // Delivery may succeed even if the HTTP response is lost. Never automatically
  // retry or retain a secret in the input box after dispatch.
  delete jobInputs.value[job.id]
  try {
    await api.writeJobInput(job.id, text, eof)
    if (current === generation) await load()
  } catch {
    if (current === generation)
      actionError.value = t('无法确认进程输入已送达，请先查看输出，不要直接重复发送。')
  } finally {
    sendingInput.value.delete(job.id)
  }
}
watch(
  () => `${session.activeSessionId}:${visible.value}`,
  () => {
    cleanup()
    tasks.value = []
    jobs.value = []
    error.value = ''
    actionError.value = ''
    void load()
  },
  { immediate: true },
)
watch(
  () =>
    (run.turns[session.activeSessionId || ''] || [])
      .map((turn) => `${turn.id}:${turn.status}:${turn.steps.length}`)
      .join('|'),
  () => {
    if (visible.value) void load()
  },
)
onUnmounted(cleanup)
</script>
<template>
  <section class="jobs-tab">
    <RunWorkspace ref="pages" :session-id="session.activeSessionId || ''" :turns="turns">
      <header>
        <strong>{{ $t('运行') }}</strong
        ><button class="icon-button" :title="$t('刷新运行状态')" @click="load">
          <IconSymbol name="refresh" :size="16" />
        </button>
      </header>
      <p v-if="error || actionError" class="empty-copy" role="alert">
        {{ actionError || error
        }}<button class="text-button" @click="load">
          {{ $t('刷新运行状态') }}
        </button>
      </p>
      <InkLoader v-else-if="loading && !tasks.length" />
      <div v-else-if="!activeTasks.length" class="runtime-empty">
        <IconSymbol name="activity" :size="24" /><strong>{{ $t('现在没有运行中的任务') }}</strong>
        <p>{{ $t('开始对话后，当前操作和等待原因会显示在这里。') }}</p>
      </div>
      <RunEntry
        v-for="task in activeTasks"
        :key="task.id"
        :task="task"
        :turn="turnById.get(task.id)"
        active
        :label="$t(labels[task.status])"
        :operation="operation(task)"
        :stopping="session.stoppingTaskIds.includes(task.id)"
        @stop="stop(task)"
        @open-subagent="openSubagent"
      />
      <article v-for="job in jobs" :key="job.id">
        <span class="section-label"
          >{{ $t('命令进程') }} · {{ $t(job.live ? '运行中' : '已结束') }}</span
        >
        <strong>{{ job.command }}</strong>
        <pre v-if="job.log_tail">{{ job.log_tail.split('\n').slice(-200).join('\n') }}</pre>
        <p v-else-if="job.live && !job.live_logs_available">
          {{ $t('此进程暂不提供实时输出。') }}
        </p>
        <form
          v-if="job.live && job.stdin_available && session.capabilities.process_input"
          class="process-input"
          @submit.prevent="writeInput(job)"
        >
          <label :for="`stdin-${job.id}`">{{ $t('发送一行标准输入（不是终端）') }}</label>
          <input
            :id="`stdin-${job.id}`"
            v-model="jobInputs[job.id]"
            type="password"
            autocomplete="off"
            :disabled="sendingInput.has(job.id)"
            maxlength="4095"
          >
          <button class="secondary-button" :disabled="sendingInput.has(job.id)" type="submit">
            {{ $t('发送输入') }}
          </button>
          <button
            class="text-button"
            :disabled="sendingInput.has(job.id)"
            type="button"
            @click="writeInput(job, true)"
          >
            {{ $t('关闭输入') }}
          </button>
          <p>{{ $t('此输入仅发给正在运行的命令，可能出现在命令输出中。') }}</p>
        </form>
        <button
          v-if="job.live && session.capabilities.process_stop"
          class="secondary-button"
          :disabled="stoppingJobs.has(job.id)"
          @click="stopJob(job)"
        >
          {{ $t(stoppingJobs.has(job.id) ? '请求停止中…' : '停止此进程') }}
        </button>
        <p v-if="job.live" class="empty-copy">
          {{ $t('停止进程会返回工具结果；若要结束整轮执行，请停止上方任务。') }}
        </p>
      </article>
      <div v-if="history.length" class="runtime-history">
        <span class="section-label">{{ $t('历史运行') }}</span>
        <RunEntry
          v-for="task in history.slice(0, historyLimit)"
          :key="task.id"
          :task="task"
          :turn="turnById.get(task.id)"
          :label="$t(labels[task.status])"
          @open-subagent="openSubagent"
        />
        <button
          v-if="history.length > historyLimit"
          type="button"
          class="text-button"
          @click="historyLimit += 20"
        >
          {{ $t('显示更多记录') }}
        </button>
      </div>
    </RunWorkspace>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/jobs-tab';
</style>
