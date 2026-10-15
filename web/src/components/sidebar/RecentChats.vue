<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { Conversation } from '@/api/client'
import { t } from '@/i18n'
import { useSessionStore } from '@/stores/session'
import { useWorkspaceStore } from '@/stores/workspace'
import { useInboxStore } from '@/stores/inbox'
import SessionItem from './SessionItem.vue'
import WorkspaceDeleteButton from './WorkspaceDeleteButton.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
const props = withDefaults(defineProps<{ query?: string; creating?: boolean }>(), {
  query: '',
  creating: false,
})
const emit = defineEmits<{ select: []; create: [folderId: string] }>()
const session = useSessionStore(),
  workspace = useWorkspaceStore(),
  router = useRouter()
const error = ref('')
const inbox = useInboxStore()
const blockedIds = computed(
  () =>
    new Set(
      inbox.items
        .filter((item) => !item.closed && !item.verdict && item.answer === undefined)
        .map((item) => item.sessionId),
    ),
)
function dateGroups(items: Conversation[]) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const week = new Date(today)
  week.setDate(week.getDate() - ((week.getDay() + 6) % 7))
  return [
    {
      id: 'today',
      label: '今天',
      items: items.filter((item) => Date.parse(item.updated_at) >= +today),
    },
    {
      id: 'week',
      label: '本周',
      items: items.filter(
        (item) => Date.parse(item.updated_at) < +today && Date.parse(item.updated_at) >= +week,
      ),
    },
    {
      id: 'older',
      label: '更早',
      items: items.filter((item) => !(Date.parse(item.updated_at) >= +week)),
    },
  ].filter((group) => group.items.length)
}
const showAll = ref<Record<string, boolean>>({})
const searching = computed(() => !!props.query.trim())
const groups = computed(() => {
  const needle = props.query.trim().toLocaleLowerCase()
  const byFolder = new Map<string, Conversation[]>()
  const ordered = session.sessions
    .filter((item) => Boolean(item.archived) === session.showArchived)
    .slice()
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
  for (const item of ordered) {
    const id = item.folder_id || ''
    const list = byFolder.get(id) || []
    list.push(item)
    byFolder.set(id, list)
  }
  const folders = workspace.folders.length
    ? workspace.folders
    : [{ id: '', name: '', root_path: '' }]
  return folders
    .map((folder) => {
      const label = folder.id ? folder.name : t('默认工作区')
      const matchedFolder =
        !!needle &&
        (label.toLocaleLowerCase().includes(needle) ||
          folder.root_path.toLocaleLowerCase().includes(needle))
      const items = (byFolder.get(folder.id) || []).filter(
        (item) => !needle || matchedFolder || item.title.toLocaleLowerCase().includes(needle),
      )
      // Always expose the open chat and running tasks, even beyond the recent-history quota.
      const visible =
        searching.value || showAll.value[folder.id]
          ? items
          : items.filter(
              (item, index) =>
                index < 5 ||
                item.id === session.activeSessionId ||
                session.runningTaskIds[item.id]?.length ||
                blockedIds.value.has(item.id),
            )
      return {
        ...folder,
        label,
        sessions: items,
        visible,
        dates: dateGroups(visible),
        hidden: items.length - visible.length,
        matchedFolder,
      }
    })
    .filter(
      (group) =>
        (!needle || group.matchedFolder || group.sessions.length) &&
        (!session.showArchived || group.sessions.length),
    )
})
watch(
  () => session.activeSessionId,
  (id) => {
    if (id)
      workspace.setExpanded(
        session.activeSession ? session.activeSession.folder_id || '' : workspace.selectedId,
        true,
      )
  },
)
async function selectGroup(id: string) {
  error.value = ''
  try {
    if (workspace.selectedId !== id || router.currentRoute.value.name !== 'Chat') {
      await router.push('/chat')
      workspace.select(id)
    }
    workspace.setExpanded(id, true)
    emit('select')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  }
}
</script>
<template>
  <div class="recent-chats">
    <p
      v-if="session.isLoading && !session.sessions.length"
      class="empty-copy"
    >
      {{ $t('正在读取会话…') }}
    </p>
    <p
      v-if="session.error || error"
      class="empty-copy"
      role="alert"
    >
      {{ session.error || error }}
      <button
        class="secondary-button"
        @click="session.loadSessions"
      >
        {{ $t('重新连接') }}
      </button>
    </p>
    <p
      v-if="!groups.length && !session.isLoading"
      class="empty-copy"
    >
      {{ searching ? $t('没有找到相关工作区或对话') : $t('还没有归档对话') }}
    </p>
    <section
      v-for="group in groups"
      :key="group.id"
      class="workspace-group"
      :class="{ current: workspace.selectedId === group.id }"
    >
      <header class="workspace-row">
        <button
          class="group-toggle"
          :aria-label="
            $t(
              workspace.isExpanded(group.id) || searching ? '收起工作区：{0}' : '展开工作区：{0}',
              [group.label],
            )
          "
          :aria-expanded="workspace.isExpanded(group.id) || searching"
          :aria-controls="'workspace-sessions-' + (group.id || 'default')"
          :disabled="searching"
          @click="workspace.toggleExpanded(group.id)"
        >
          <IconSymbol
            name="chevron"
            :size="12"
            :class="{ expanded: workspace.isExpanded(group.id) || searching }"
          />
        </button>
        <button
          class="workspace-name"
          :aria-label="
            $t(workspace.selectedId === group.id ? '当前工作区：{0}' : '选择工作区：{0}', [
              group.label,
            ])
          "
          :aria-pressed="workspace.selectedId === group.id"
          :title="group.root_path || group.label"
          @click="selectGroup(group.id)"
        >
          <IconSymbol
            name="folder"
            :size="16"
          />
          <span>{{ group.label }}</span>
          <small>{{ group.sessions.length }}</small>
        </button>
        <button
          class="workspace-new"
          :disabled="creating"
          :aria-label="$t('在「{0}」中新建对话', [group.label])"
          :title="$t('在「{0}」中新建对话', [group.label])"
          @click="emit('create', group.id)"
        >
          <IconSymbol
            name="plus"
            :size="16"
          />
        </button>
        <WorkspaceDeleteButton
          v-if="group.id"
          :folder="group"
          :disabled="creating"
        />
      </header>
      <div
        v-if="workspace.isExpanded(group.id) || searching"
        :id="'workspace-sessions-' + (group.id || 'default')"
        class="workspace-sessions"
      >
        <p
          v-if="!group.sessions.length"
          class="empty-copy workspace-empty"
        >
          {{ session.showArchived ? $t('这个工作区还没有归档对话。') : $t('还没有对话') }}
        </p>
        <section
          v-for="date in group.dates"
          :key="date.id"
          class="session-date-group"
        >
          <h3 class="session-date-label">
            {{ $t(date.label) }}
          </h3>
          <SessionItem
            v-for="item in date.items"
            :key="item.id"
            :item="item"
            :collapsed="false"
            @select="emit('select')"
          />
        </section>
        <button
          v-if="group.hidden && !searching"
          class="show-more"
          @click="showAll[group.id] = true"
        >
          {{ $t('显示更多对话（{0}）', [group.hidden]) }}
        </button>
        <button
          v-else-if="showAll[group.id] && group.sessions.length > 5 && !searching"
          class="show-more"
          @click="showAll[group.id] = false"
        >
          {{ $t('收起对话列表') }}
        </button>
      </div>
    </section>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/workspace-list';
</style>
