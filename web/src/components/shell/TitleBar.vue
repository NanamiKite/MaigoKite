<script setup lang="ts">
import { t } from '@/i18n'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useInboxStore } from '@/stores/inbox'
import { usePulse } from '@/composables/usePulse'
import { overlayPanel } from '@/utils/layout-media'
import { vTooltip } from '@/utils/tooltip'
import IconSymbol from '@/components/common/IconSymbol.vue'
import SessionTitle from '@/components/common/SessionTitle.vue'
import Pulse from './Pulse.vue'
import SidebarToggle from './SidebarToggle.vue'
import ConversationTabs from './ConversationTabs.vue'
defineProps<{ desktop?: boolean; sidebarOverlay?: boolean }>()
const session = useSessionStore(),
  ui = useUIStore(),
  inbox = useInboxStore(),
  route = useRoute(),
  router = useRouter()
const { state, offline, target } = usePulse()
const currentId = computed(() => (route.name === 'Chat' ? String(route.params.id || '') : ''))
const stateLabels = {
  idle: '静息',
  thinking: '思考',
  acting: '行动',
  waiting: '等你处理',
  error: '出错',
  offline: '离线',
} as const
const targetLabels = {
  waiting: '「{0}」等你处理',
  acting: '「{0}」正在行动',
  thinking: '「{0}」正在思考',
  error: '「{0}」出错',
  offline: '「{0}」连接已断开',
} as const
function titleOf(id: string) {
  return session.sessions.find((item) => item.id === id)?.title || t('未命名对话')
}
const pulseLabel = computed(() => {
  const next = target.value
  return next ? t(targetLabels[next.state], [titleOf(next.sessionId)]) : t(stateLabels[state.value])
})
const announced = computed(() => {
  const next = target.value
  return next && (next.state === 'acting' || next.state === 'thinking')
    ? t('「{0}」正在处理', [titleOf(next.sessionId)])
    : pulseLabel.value
})
const jumpable = computed(() => {
  const next = target.value
  return Boolean(next && (next.sessionId !== currentId.value || next.awaitId))
})
async function goToTarget() {
  const next = target.value
  if (!next) return
  if (ui.drawerOpen && overlayPanel('drawer')) ui.drawerOpen = false
  if (overlayPanel('sidebar') && !ui.layout.sidebarCollapsed) ui.layout.toggleSidebar()
  await router.push({
    path: '/chat/' + next.sessionId,
    query: next.awaitId ? { await: next.awaitId } : undefined,
  })
}
const title = computed(() =>
  route.name === 'Chat'
    ? session.activeSession?.title || t('新页')
    : t(String(route.meta.title || '个人空间')),
)
const otherPending = computed(
  () =>
    inbox.items.filter(
      (item) =>
        !item.closed &&
        !item.verdict &&
        item.answer === undefined &&
        item.sessionId !== currentId.value,
    ).length,
)
const drawerLabel = computed(() =>
  [
    t(ui.drawerOpen ? '收起抽屉' : '打开抽屉'),
    ...(otherPending.value ? [t('{0} 项等你处理', [otherPending.value])] : []),
  ].join(' · '),
)
function toggleDrawer() {
  if (ui.drawerOpen) ui.drawerOpen = false
  else ui.openDrawer(otherPending.value ? 'pending' : ui.drawerTab)
}
</script>
<template>
  <header
    class="titlebar"
    :class="{ offline }"
    :aria-label="$t('应用导航')"
  >
    <SidebarToggle
      v-if="!desktop"
      :pending-count="otherPending"
      :sidebar-overlay="sidebarOverlay"
    />
    <span class="pulse-slot">
      <button
        v-if="jumpable"
        v-tooltip="pulseLabel"
        class="pulse-target"
        type="button"
        :aria-label="pulseLabel"
        @click="goToTarget"
      >
        <Pulse
          :state="state"
          :size="12"
          aria-hidden="true"
        />
      </button>
      <Pulse
        v-else
        :state="state"
        :size="12"
      />
    </span>
    <span
      class="sr-only"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {{ announced }}
    </span>
    <ConversationTabs v-if="route.name === 'Chat'" />
    <SessionTitle
      v-else
      :title="title"
      :serif="route.name === 'Chat'"
      :session-id="currentId && session.activeSession?.id === currentId ? currentId : undefined"
    />
    <div
      class="title-actions"
      role="group"
      :aria-label="$t('应用操作')"
    >
      <!-- Chat owns session settings; this stable target only places its controls. -->
      <div
        v-show="route.name === 'Chat'"
        id="chat-session-actions"
        class="chat-session-actions"
      />
      <button
        v-tooltip="$t(ui.theme === 'dark' ? '切换到宣纸' : '切换到松烟')"
        class="title-button"
        type="button"
        :aria-label="$t(ui.theme === 'dark' ? '切换到宣纸' : '切换到松烟')"
        @click="ui.setAppearance({ theme: ui.theme === 'dark' ? 'light' : 'dark' })"
      >
        <IconSymbol
          :name="ui.theme === 'dark' ? 'sun' : 'moon'"
          :size="20"
        />
      </button>
      <button
        v-tooltip="drawerLabel"
        class="title-button drawer-toggle"
        type="button"
        :aria-label="drawerLabel"
        :aria-expanded="ui.drawerOpen"
        aria-controls="work-drawer"
        @click="toggleDrawer"
      >
        <IconSymbol
          name="files"
          :size="20"
        />
        <span
          v-if="otherPending"
          class="pending-count"
          :aria-label="$t('{0} 项等你处理', [otherPending])"
        >
          {{ otherPending }}
        </span>
      </button>
    </div>
  </header>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.titlebar {
  height: var(--titlebar-height);
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding-left: var(--space-3);
  border-bottom: var(--hairline) solid var(--rule);
  background: var(--paper);
  -webkit-app-region: no-drag;
  position: relative;
}
.titlebar.offline::after {
  content: '';
  height: var(--pending-line);
  position: absolute;
  inset: auto 0 0;
  background: var(--wait);
}
.pulse-slot {
  position: relative;
  display: grid;
  place-items: center;
  flex: none;
  width: var(--space-4);
  height: var(--space-4);
}
.pulse-target {
  @include mixins.interactive-row;
  position: absolute;
  inset: calc(-1 * var(--space-3));
  display: grid;
  place-items: center;
  padding: 0;
  border: var(--hairline) solid var(--transparent);
  border-radius: var(--radius-sm);
  background: var(--transparent);
}
.title-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: 100%;
  flex: none;
  padding-inline-end: var(--space-3);
}
.chat-session-actions {
  display: flex;
  align-items: center;
  flex: none;
  -webkit-app-region: no-drag;
}
.chat-session-actions:empty {
  display: none;
}
button {
  -webkit-app-region: no-drag;
}
.title-button {
  @include mixins.interactive-row;
  position: relative;
  display: grid;
  place-items: center;
  flex: none;
  width: var(--titlebar-button-size);
  height: var(--titlebar-button-size);
  padding: 0;
  border: var(--hairline) solid var(--transparent);
  border-radius: var(--radius-sm);
  background: var(--transparent);
  color: var(--ink-2);
}
.title-button[aria-expanded='true'] {
  color: var(--accent);
  background: var(--accent-selected);
}
.title-button:active {
  transform: scale(var(--press-scale));
}
.pending-count {
  display: grid;
  place-items: center;
  position: absolute;
  top: 0;
  right: 0;
  min-width: var(--pending-badge-size);
  height: var(--pending-badge-size);
  padding-inline: var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--wait-soft);
  color: var(--wait-ink);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  box-shadow: 0 0 0 var(--hairline) var(--paper);
}
</style>
