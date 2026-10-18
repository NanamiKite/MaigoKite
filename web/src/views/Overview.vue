<script setup lang="ts">
import { i18n, t } from '@/i18n'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useRunStore } from '@/stores/run'
import { useInboxStore } from '@/stores/inbox'
import { useWorkspaceStore } from '@/stores/workspace'
import type { AwaitItem } from '@/api/events'
import { pendingItem, pendingOrder } from '@/utils/rail-presentation'
import { approvalDescription } from '@/utils/tool-presentation'
import { knownSessionStatus } from '@/utils/work-records'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import Pulse from '@/components/shell/Pulse.vue'
import WorkspaceSelector from '@/components/sidebar/WorkspaceSelector.vue'
const session = useSessionStore(),
  run = useRunStore(),
  inbox = useInboxStore()
const workspace = useWorkspaceStore(),
  router = useRouter()
const creating = ref(false),
  ready = ref(false)
const pending = computed(() => inbox.items.filter(pendingItem).sort(pendingOrder))
const records = computed(() =>
  session.sessions
    .filter((item) => !item.archived)
    .slice()
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
    .map((item) => ({
      ...item,
      status: knownSessionStatus(run.turns[item.id] || [], session.runningTaskIds[item.id] || []),
    })),
)
const active = computed(() =>
  records.value.filter((item) =>
    ['running', 'waiting', 'waiting_approval', 'pending'].includes(item.status || ''),
  ),
)
const recent = computed(() => records.value.slice(0, 12))
const statusLabels: Record<string, string> = {
  running: '运行中',
  pending: '等待开始',
  waiting: '等你处理',
  waiting_approval: '等你处理',
  completed: '已完成',
  succeeded: '已完成',
  failed: '执行失败',
  cancelled: '已停止',
  interrupted: '已中断',
  timed_out: '已超时',
}
function statusLabel(value?: string) {
  return t(value ? statusLabels[value] || '状态尚未读取' : '状态尚未读取')
}
function folder(id?: string) {
  return workspace.folders.find((item) => item.id === (id || ''))?.name || t('未选择工作区')
}
function operation(id: string) {
  const turn = run.turns[id]?.at(-1)
  return (
    turn?.steps.find((step) => step.status === 'running')?.title ||
    statusLabel(turn?.status || 'running')
  )
}
function pendingCopy(item: AwaitItem) {
  if (item.mode === 'question')
    return { action: item.title || t('Agent 在等你回答'), target: t('待回答') }
  return approvalDescription(
    item,
    session.approvals[item.sessionId]?.find((record) => record.id === item.id),
  )
}
function updated(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return t('刚刚')
  return new Intl.DateTimeFormat(i18n.global.locale.value, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
async function refresh() {
  if (session.isLoading) return
  ready.value = false
  await session.loadSessions()
  ready.value = !session.isLoading && !session.error
}
watch(
  () => session.isLoading,
  (loading, previous) => {
    if (previous && !loading) ready.value = !session.error
  },
)
watch([ready, () => session.sessions.length, () => session.error], () => {
  // Never redirect on loading/error or merely because all conversations are archived.
  if (ready.value && !session.isLoading && !session.error && !session.sessions.length)
    void router.replace('/chat')
})
onMounted(() => {
  void refresh()
})
async function create() {
  if (creating.value) return
  creating.value = true
  try {
    const item = await session.createSession()
    await router.push('/chat/' + item.id)
  } catch {
    /* The existing store owns submission errors. */
  } finally {
    creating.value = false
  }
}
</script>
<template>
  <main class="page-shell overview">
    <header class="record-heading">
      <h1>{{ $t('工作记录') }}</h1>
      <button
        class="record-new"
        :disabled="creating"
        :aria-busy="creating || undefined"
        @click="create"
      >
        <IconSymbol
          name="compose"
          :size="18"
        />{{ creating ? $t('正在创建') : $t('新建对话') }}
      </button>
    </header>
    <p
      v-if="session.error"
      class="record-error"
      role="alert"
    >
      {{ session.error
      }}<button
        class="secondary-button"
        :disabled="session.isLoading"
        @click="refresh"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <div
      v-if="session.isLoading && !session.sessions.length"
      class="record-loading"
    >
      <InkLoader /><InkLoader /><InkLoader />
    </div>
    <section
      v-if="pending.length"
      class="record-section record-pending"
    >
      <header>
        <h2>{{ $t('等你处理') }}</h2>
        <small>{{ pending.length }}</small>
      </header>
      <router-link
        v-for="item in pending"
        :key="item.id"
        class="record-row"
        :to="{ path: '/chat/' + item.sessionId, query: { await: item.id } }"
      >
        <Pulse
          state="waiting"
          :size="14"
        /><span class="record-copy"><strong>{{ pendingCopy(item).action }}</strong>
          <small>{{
            session.sessions.find((entry) => entry.id === item.sessionId)?.title ||
              $t('未命名对话')
          }}
            · {{ pendingCopy(item).target }}</small></span><IconSymbol
          name="chevron"
          :size="14"
        />
      </router-link>
    </section>
    <p
      v-if="inbox.error"
      class="record-error"
      role="alert"
    >
      {{ inbox.error
      }}<button
        class="secondary-button"
        @click="inbox.refresh"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <section
      v-if="active.length"
      class="record-section"
    >
      <header>
        <h2>{{ $t('运行中的对话') }}</h2>
      </header>
      <router-link
        v-for="item in active"
        :key="item.id"
        class="record-row"
        :to="'/chat/' + item.id"
      >
        <Pulse
          :state="run.pulse(item.id)"
          :size="14"
        /><span class="record-copy"><strong>{{ item.title || $t('未命名对话') }}</strong><small>{{ operation(item.id) }}</small></span><span class="record-status">{{ statusLabel(item.status) }}</span>
      </router-link>
    </section>
    <section
      v-if="session.sessions.length"
      class="record-section"
    >
      <header>
        <h2>{{ $t('最近对话') }}</h2>
        <button
          class="icon-button"
          :aria-label="$t('刷新会话')"
          :disabled="session.isLoading"
          @click="refresh"
        >
          <IconSymbol
            name="refresh"
            :size="16"
          />
        </button>
      </header>
      <router-link
        v-for="item in recent"
        :key="item.id"
        class="record-row"
        :to="'/chat/' + item.id"
      >
        <IconSymbol
          name="review"
          :size="18"
        /><span class="record-copy"><strong>{{ item.title || $t('未命名对话') }}</strong><small>{{ folder(item.folder_id) }} ·
          <time :datetime="item.updated_at">{{ updated(item.updated_at) }}</time></small></span><span class="record-status">{{ statusLabel(item.status) }}</span>
      </router-link>
      <p
        v-if="!recent.length"
        class="record-archived"
      >
        {{ $t('对话已归档，可从侧栏查看。') }}
      </p>
    </section>
    <section class="record-section workspace-record">
      <header>
        <h2>{{ $t('当前工作区') }}</h2>
        <WorkspaceSelector
          collapsed
          presentation-trigger
        />
      </header>
      <strong>{{ workspace.current?.name || $t('未选择工作区') }}</strong>
      <p
        v-if="workspace.current?.root_path"
        class="workspace-path"
      >
        {{ workspace.current.root_path }}
      </p>
      <p
        v-else
        class="record-archived"
      >
        {{
          workspace.loading ? $t('正在读取工作区…') : $t('选择一个文件夹，文件操作会在这里进行。')
        }}
      </p>
      <p
        v-if="workspace.error"
        class="record-error"
        role="alert"
      >
        {{ workspace.error
        }}<button
          class="secondary-button"
          @click="workspace.load"
        >
          {{ $t('重试') }}
        </button>
      </p>
    </section>
  </main>
</template>
<style scoped lang="scss">
@use '@/styles/overview';
</style>
