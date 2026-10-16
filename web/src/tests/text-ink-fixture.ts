// Isolated visual data for the real UI; no backend writes or changes to existing fixtures.
import { install as installTurn } from './stage3b-fixture'
import pinia from '@/stores'
import router from '@/router'
import { useRunStore } from '@/stores/run'
import { useUIStore, type Accent, type ThemeMode } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import { api, type WorkspaceChange } from '@/api/client'

export async function install(scene: string, theme: ThemeMode, accent: Accent) {
  const change: WorkspaceChange = {
    path: 'notes/config.md',
    status: 'modified',
    additions: 1,
    deletions: 1,
    diff: '--- a/notes/config.md\n+++ b/notes/config.md\n@@ -1 +1 @@\n-old\n+new',
    diff_truncated: false,
  }
  api.listWorkspaceChanges = async () => ({ changes: [change], git: true, truncated: false })
  api.getWorkspaceDiff = async () => change
  await installTurn(
    scene === 'waiting' ? 'waiting' : scene === 'failure' ? 'failure' : 'process',
    theme,
  )
  const ui = useUIStore(pinia),
    run = useRunStore(pinia)
  ui.setAppearance({ theme, accent, texture: true })
  ui.layout.sidebarCollapsed = false
  useWorkspaceStore(pinia).folders = [{ id: '', name: 'MaigoKite', root_path: '/demo/workspace' }]
  run.turns.visual[0].text += '\n\n[使用说明](https://example.org/guide) · 查看配置与操作记录。'
  run.turns.visual[0].plan = [
    { content: '核对配置', status: 'completed' },
    { content: '整理说明', status: 'in_progress' },
  ]
  if (['chat', 'waiting', 'failure', 'model-menu', 'changes'].includes(scene)) {
    ui.drawerTab = scene === 'waiting' ? 'pending' : scene === 'changes' ? 'changes' : 'plan'
    ui.drawerOpen = true
  } else if (scene === 'palette') {
    ui.commandOpen = true
  } else {
    ui.drawerOpen = false
    await router.replace(
      scene === 'settings-select'
        ? '/settings?tab=general'
        : scene.startsWith('settings-')
          ? `/settings?tab=${scene.slice(9)}`
          : `/${scene}`,
    )
  }
}
