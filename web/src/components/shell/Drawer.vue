<script setup lang="ts">
import { vDeskPaper } from '@/utils/desk-paper'
import { computed, nextTick, ref, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import ChangesTab from '@/components/drawer/ChangesTab.vue'
import JobsTab from '@/components/drawer/JobsTab.vue'
import PlanTab from '@/components/drawer/PlanTab.vue'
import PendingTab from '@/components/drawer/PendingTab.vue'
import { useInboxStore } from '@/stores/inbox'
import { pendingItem } from '@/utils/rail-presentation'
import FileExplorer from '@/components/layout/FileExplorer.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import ResizeHandle from '@/components/common/ResizeHandle.vue'
defineProps<{ maximumWidth?: number }>()
const ui = useUIStore(),
  workspace = useWorkspaceStore()
const wide = ref(false),
  resizing = ref(false),
  everOpened = ref(ui.drawerOpen)
const tabList = ref<HTMLElement | null>(null)
const content = ref<HTMLElement | null>(null)
const scrollPositions: Partial<Record<typeof ui.drawerTab, number>> = {}
let opener: HTMLElement | null = null
const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'
const tabs = [
  { id: 'pending', label: '等你处理', icon: 'pending', component: PendingTab },
  { id: 'changes', label: '变更', icon: 'review', component: ChangesTab },
  { id: 'jobs', label: '运行', icon: 'activity', component: JobsTab },
  { id: 'plan', label: '计划', icon: 'check', component: PlanTab },
  { id: 'files', label: '文件', icon: 'files', component: FileExplorer },
] as const
const inbox = useInboxStore()
const pendingCount = computed(() => inbox.items.filter(pendingItem).length)
const current = computed(() => tabs.find((tab) => tab.id === ui.drawerTab) ?? tabs[0])
watch(
  () => ui.drawerOpen,
  async (open) => {
    if (open) {
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
      everOpened.value = true
      await nextTick()
      tabList.value?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus()
    } else {
      wide.value = false
      resizing.value = false
      await nextTick()
      if (opener?.isConnected && !opener.closest('[inert]') && opener.getClientRects().length)
        opener.focus({ preventScroll: true })
    }
  },
)
watch(
  () => ui.drawerTab,
  async (next, previous) => {
    scrollPositions[previous] = content.value?.scrollTop ?? 0
    await nextTick()
    if (content.value && ui.drawerTab === next) content.value.scrollTop = scrollPositions[next] ?? 0
  },
)
async function moveTab(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const index = tabs.findIndex((tab) => tab.id === ui.drawerTab)
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
  ui.drawerTab = tabs[next].id
  await nextTick()
  tabList.value?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus()
}
</script>
<template>
  <div
    v-if="ui.drawerOpen"
    class="drawer-shade"
    @click="ui.drawerOpen = false"
  />
  <aside
    v-if="everOpened"
    v-show="ui.drawerOpen"
    id="work-drawer"
    v-desk-paper="ui.drawerOpen"
    class="drawer"
    :class="{ wide, resizing }"
    :aria-label="$t('工作抽屉')"
  >
    <header class="drawer-heading">
      <div
        class="drawer-workspace"
        :title="ui.drawerTab === 'pending' ? undefined : workspace.current?.root_path"
      >
        <IconSymbol
          :name="ui.drawerTab === 'pending' ? 'pending' : 'folder'"
          :size="16"
        />
        <span>
          {{
            ui.drawerTab === 'pending' ? $t('所有对话') : workspace.current?.name || $t('工作区')
          }}
        </span>
      </div>
      <div class="drawer-controls">
        <button
          class="drawer-control"
          :aria-label="wide ? $t('还原面板') : $t('放大面板')"
          :title="wide ? $t('还原面板') : $t('放大面板')"
          :aria-pressed="wide"
          @click="wide = !wide"
        >
          <IconSymbol
            :name="wide ? 'restore' : 'expand'"
            :size="18"
          />
        </button>
        <button
          class="drawer-control"
          :aria-label="$t('关闭抽屉')"
          :title="$t('关闭抽屉') + ' (' + mod + '+.)'"
          @click="ui.drawerOpen = false"
        >
          <IconSymbol
            name="close"
            :size="18"
          />
        </button>
      </div>
    </header>
    <nav
      ref="tabList"
      class="drawer-tabs"
      role="tablist"
      :aria-label="$t('工作抽屉')"
      @keydown="moveTab"
    >
      <button
        v-for="tab in tabs"
        :id="'drawer-tab-' + tab.id"
        :key="tab.id"
        role="tab"
        :tabindex="ui.drawerTab === tab.id ? 0 : -1"
        :aria-selected="ui.drawerTab === tab.id"
        aria-controls="drawer-content"
        :class="{ active: ui.drawerTab === tab.id }"
        @click="ui.drawerTab = tab.id"
      >
        <IconSymbol
          :name="tab.icon"
          :size="15"
        />
        <span>{{ $t(tab.label) }}</span>
        <small
          v-if="tab.id === 'pending' && pendingCount"
          class="tab-count"
        >
          {{ pendingCount }}
        </small>
      </button>
    </nav>
    <div
      id="drawer-content"
      ref="content"
      class="drawer-content"
      role="tabpanel"
      :aria-labelledby="'drawer-tab-' + current.id"
      tabindex="0"
    >
      <!-- Keep tab content alive so file selection and scroll positions survive panel toggles. -->
      <KeepAlive :max="5">
        <component
          :is="current.component"
          :embedded="current.id === 'files' || undefined"
        />
      </KeepAlive>
    </div>
    <ResizeHandle
      v-if="ui.drawerOpen && !wide"
      side="right"
      :maximum="maximumWidth"
      @dragging="resizing = $event"
    />
  </aside>
</template>
<style scoped lang="scss">
@use '@/styles/drawer';
</style>
