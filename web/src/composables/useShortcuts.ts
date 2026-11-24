import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/session'
import { useInboxStore } from '@/stores/inbox'
import { revealRailNode } from '@/utils/rail-navigation'
import { overlayPanel } from '@/utils/layout-media'
import { pendingItem } from '@/utils/rail-presentation'
import { awaitCardVisible } from '@/utils/await-focus'
export function useShortcuts() {
  const ui = useUIStore(),
    session = useSessionStore(),
    inbox = useInboxStore(),
    router = useRouter()
  let nodeIndex = -1
  async function newPage() {
    const item = await session.createSession()
    await router.push('/chat/' + item.id)
  }
  function onKey(event: KeyboardEvent) {
    if (event.isComposing || event.defaultPrevented) return
    const target = event.target as HTMLElement | null
    const editing = Boolean(target?.closest('input,textarea,select,[contenteditable="true"]'))
    const modifier = event.ctrlKey || event.metaKey,
      key = event.key.toLowerCase()
    // Native dialogs own their keyboard/focus scope; no background decisions.
    if (document.querySelector('dialog[open]')) return
    if (modifier && ['k', 'j', 'n', 'b', '.', '/'].includes(key)) {
      event.preventDefault()
      if (key === 'k') ui.commandOpen = !ui.commandOpen
      if (key === 'j') {
        if (ui.drawerOpen && ui.drawerTab === 'pending') ui.drawerOpen = false
        else ui.openDrawer('pending')
      }
      if (key === 'n') void newPage().catch(() => undefined)
      if (key === 'b') ui.layout.toggleSidebar()
      if (key === '.') ui.drawerOpen = !ui.drawerOpen
      if (key === '/') ui.shortcutsOpen = !ui.shortcutsOpen
      return
    }
    if (key === 'escape') {
      if (ui.drawerOpen || ui.commandOpen || ui.shortcutsOpen) ui.closeOverlays()
      else if (overlayPanel('sidebar') && !ui.layout.sidebarCollapsed) ui.layout.toggleSidebar()
      else if (!editing && router.currentRoute.value.name === 'Chat') {
        void session.cancelActiveTask().catch(() => undefined)
      }
      return
    }
    if (editing || modifier || event.altKey) return
    const inDrawer = Boolean(target?.closest('#work-drawer'))
    const drawerScope = inDrawer && ui.drawerOpen && ui.drawerTab === 'pending'
    if (inDrawer && !drawerScope) return
    const scope = document.querySelector<HTMLElement>(
      drawerScope ? '#work-drawer' : 'main.main-content',
    )
    if (!scope || scope.closest('[inert]') || target?.closest('#session-sidebar')) return
    if (['y', 'n'].includes(key)) {
      if (event.repeat) return
      const focusedId = target?.closest<HTMLElement>('[data-await]')?.dataset.await
      const card = inbox.items
        .filter(
          (item) =>
            pendingItem(item) &&
            item.mode === 'approval' &&
            (drawerScope || item.sessionId === session.activeSessionId) &&
            (!focusedId || item.id === focusedId),
        )
        .map((item) => scope.querySelector<HTMLElement>(`[data-await="${CSS.escape(item.id)}"]`))
        .find(awaitCardVisible)
      if (card) {
        event.preventDefault()
        card
          .querySelector<HTMLButtonElement>(
            `[data-verdict="${key === 'y' ? 'allow' : 'deny'}"]:not(:disabled)`,
          )
          ?.click()
      }
    }
    if (key === '[' || key === ']') {
      const nodes = Array.from(scope?.querySelectorAll<HTMLButtonElement>('[data-rail-node]') || [])
      nodeIndex = Math.max(0, Math.min(nodes.length - 1, nodeIndex + (key === ']' ? 1 : -1)))
      revealRailNode(nodes[nodeIndex] || null)
      event.preventDefault()
    }
  }
  onMounted(() => window.addEventListener('keydown', onKey))
  onUnmounted(() => window.removeEventListener('keydown', onKey))
}
