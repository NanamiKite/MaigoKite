<script setup lang="ts">
import { vDeskPaper } from '@/utils/desk-paper'
import { nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useSettingsStore } from '@/stores/settings'
import { useUIStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import WorkspaceSelector from '@/components/sidebar/WorkspaceSelector.vue'
import RecentChats from '@/components/sidebar/RecentChats.vue'
import ResizeHandle from '@/components/common/ResizeHandle.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { overlayPanel } from '@/utils/layout-media'
import { vTooltip } from '@/utils/tooltip'
defineProps<{ maximumWidth?: number }>()
const session = useSessionStore(),
  settings = useSettingsStore(),
  ui = useUIStore(),
  workspace = useWorkspaceStore(),
  router = useRouter()
const resizing = ref(false),
  searchOpen = ref(false),
  query = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'
async function create(folderId?: string) {
  if (folderId !== undefined) workspace.select(folderId)
  await router.push('/chat')
  closeSearch()
  closeOnNarrowScreen()
}
async function search() {
  if (ui.layout.sidebarCollapsed) ui.layout.toggleSidebar()
  searchOpen.value = true
  await nextTick()
  searchInput.value?.focus()
}
function closeSearch() {
  query.value = ''
  searchOpen.value = false
}
function toggleArchive() {
  if (ui.layout.sidebarCollapsed) ui.layout.toggleSidebar()
  session.showArchived = !session.showArchived
  query.value = ''
}
function closeOnNarrowScreen() {
  if (overlayPanel('sidebar') && !ui.layout.sidebarCollapsed) ui.layout.toggleSidebar()
}
</script>
<template>
  <aside
    id="session-sidebar"
    v-desk-paper="ui.layout.sidebarCollapsed"
    class="sidebar"
    :class="{ collapsed: ui.layout.sidebarCollapsed, resizing }"
    :aria-label="$t('会话侧栏')"
  >
    <div class="identity">
      <span
        v-if="ui.layout.sidebarCollapsed"
        class="brand-button"
      >
        <AppIcon v-if="ui.appearance.avatar === '助'" />
        <span
          v-else
          class="avatar"
        >
          {{ ui.appearance.avatar }}
        </span>
      </span>
      <template v-else>
        <AppIcon v-if="ui.appearance.avatar === '助'" />
        <span
          v-else
          class="avatar"
        >
          {{ ui.appearance.avatar }}
        </span>
        <strong :title="settings.profile.assistantName">
          {{ settings.profile.assistantName }}
        </strong>
      </template>
    </div>
    <button
      v-tooltip="ui.layout.sidebarCollapsed ? $t('新页') + ' (' + mod + '+N)' : undefined"
      class="new-page"
      :aria-label="$t('新页')"
      @click="create()"
    >
      <IconSymbol
        name="compose"
        :size="18"
      />
      <span v-if="!ui.layout.sidebarCollapsed">{{ $t('新页') }}</span>
      <kbd v-if="!ui.layout.sidebarCollapsed">{{ mod }} N</kbd>
    </button>
    <button
      v-tooltip="$t('搜索工作区和对话')"
      class="sidebar-action"
      :aria-label="$t('搜索工作区和对话')"
      :aria-expanded="searchOpen && !ui.layout.sidebarCollapsed"
      aria-controls="session-search"
      @click="search"
    >
      <IconSymbol
        name="search"
        :size="18"
      />
      <span v-if="!ui.layout.sidebarCollapsed">{{ $t('搜索') }}</span>
    </button>
    <button
      v-if="ui.layout.sidebarCollapsed"
      v-tooltip="$t('归档对话')"
      class="sidebar-action"
      :aria-label="$t('归档对话')"
      :aria-pressed="session.showArchived"
      @click="toggleArchive"
    >
      <IconSymbol
        name="archive"
        :size="18"
      />
    </button>
    <template v-if="!ui.layout.sidebarCollapsed">
      <div
        v-if="searchOpen"
        id="session-search"
        class="session-search"
      >
        <input
          ref="searchInput"
          v-model="query"
          type="search"
          maxlength="500"
          :aria-label="$t('搜索工作区和对话')"
          :placeholder="$t('搜索工作区和对话')"
          @keydown.esc.stop="closeSearch"
        />
        <button
          v-tooltip="$t('关闭搜索')"
          class="sidebar-icon"
          :aria-label="$t('关闭搜索')"
          @click="closeSearch"
        >
          <IconSymbol
            name="close"
            :size="14"
          />
        </button>
      </div>
    </template>
    <div class="session-heading">
      <WorkspaceSelector :collapsed="ui.layout.sidebarCollapsed" />
      <div
        v-if="!ui.layout.sidebarCollapsed"
        class="session-list-controls"
      >
        <button
          v-tooltip="session.showArchived ? $t('查看当前对话') : $t('查看归档对话')"
          class="sidebar-icon"
          :aria-pressed="session.showArchived"
          :aria-label="session.showArchived ? $t('查看当前对话') : $t('查看归档对话')"
          @click="toggleArchive"
        >
          <IconSymbol
            name="archive"
            :size="16"
          />
        </button>
        <button
          v-tooltip="$t('刷新会话')"
          class="sidebar-icon"
          :disabled="session.isLoading"
          :aria-busy="session.isLoading || undefined"
          :aria-label="$t('刷新会话')"
          @click="session.loadSessions"
        >
          <IconSymbol
            name="refresh"
            :size="16"
          />
        </button>
      </div>
    </div>
    <RecentChats
      v-show="!ui.layout.sidebarCollapsed"
      :query="query"
      @create="create"
      @select="closeOnNarrowScreen"
    />
    <footer>
      <router-link
        v-tooltip="ui.layout.sidebarCollapsed ? $t('工作记录') : undefined"
        to="/overview"
        :aria-label="$t('工作记录')"
        @click="closeOnNarrowScreen"
      >
        <IconSymbol
          name="review"
          :size="18"
        />
        <span v-if="!ui.layout.sidebarCollapsed">{{ $t('工作记录') }}</span>
      </router-link>
      <router-link
        v-tooltip="ui.layout.sidebarCollapsed ? $t('扩展') : undefined"
        to="/integrations"
        :aria-label="$t('扩展')"
        @click="closeOnNarrowScreen"
      >
        <IconSymbol
          name="cube"
          :size="18"
        />
        <span v-if="!ui.layout.sidebarCollapsed">{{ $t('扩展') }}</span>
      </router-link>
      <router-link
        v-tooltip="ui.layout.sidebarCollapsed ? $t('它记得') : undefined"
        to="/memory"
        :aria-label="$t('它记得')"
        @click="closeOnNarrowScreen"
      >
        <span
          class="memory-mark"
          aria-hidden="true"
        >
          ✦
        </span>
        <span v-if="!ui.layout.sidebarCollapsed">{{ $t('它记得') }}</span>
      </router-link>
      <router-link
        v-tooltip="ui.layout.sidebarCollapsed ? $t('设置') : undefined"
        to="/settings"
        :aria-label="$t('设置')"
        @click="closeOnNarrowScreen"
      >
        <IconSymbol
          name="settings"
          :size="18"
        />
        <span v-if="!ui.layout.sidebarCollapsed">{{ $t('设置') }}</span>
      </router-link>
    </footer>
    <ResizeHandle
      v-if="!ui.layout.sidebarCollapsed"
      side="left"
      :maximum="maximumWidth"
      @dragging="resizing = $event"
    />
  </aside>
</template>
<style scoped lang="scss">
@use '@/styles/sidebar';
</style>
