import { computed, onActivated, onDeactivated, onScopeDispose, ref, watch } from 'vue'
import { api, type WorkspaceChange } from '@/api/client'
import { useWorkspaceStore } from '@/stores/workspace'
import { useSessionStore } from '@/stores/session'
import { useRunStore } from '@/stores/run'

const REFRESH_DELAY_MS = 250

export function useWorkspaceChanges(visible: () => boolean) {
  const workspace = useWorkspaceStore(),
    session = useSessionStore(),
    run = useRunStore()
  const changes = ref<WorkspaceChange[]>([]),
    selectedPath = ref('')
  const loading = ref(false),
    error = ref(''),
    gitAvailable = ref(true)
  const changesTruncated = ref(false),
    diffLoading = ref(false),
    diffError = ref('')
  const activated = ref(true)
  const enabled = computed(() => visible() && activated.value)
  const selected = computed(() => changes.value.find((item) => item.path === selectedPath.value))
  let listController: AbortController | undefined, diffController: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let refreshAgain = false

  function scheduleRefresh() {
    if (!enabled.value) return
    if (loading.value) {
      refreshAgain = true
      return
    }
    // Bound event bursts without postponing refresh indefinitely while a task runs.
    if (timer) return
    timer = setTimeout(() => {
      timer = undefined
      void load()
    }, REFRESH_DELAY_MS)
  }

  async function selectChange(path: string) {
    if (!enabled.value) return
    diffController?.abort()
    const controller = (diffController = new AbortController())
    const folderId = workspace.selectedId
    selectedPath.value = path
    diffLoading.value = true
    diffError.value = ''
    try {
      const detail = await api.getWorkspaceDiff(path, folderId, controller.signal)
      if (controller.signal.aborted || folderId !== workspace.selectedId) return
      const index = changes.value.findIndex((item) => item.path === path)
      if (index >= 0) changes.value[index] = detail
    } catch (reason) {
      if (!controller.signal.aborted)
        diffError.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      if (diffController === controller) diffLoading.value = false
    }
  }

  async function load() {
    if (!enabled.value) return
    if (loading.value) {
      refreshAgain = true
      return
    }
    if (timer) clearTimeout(timer)
    timer = undefined
    refreshAgain = false
    const controller = (listController = new AbortController())
    const folderId = workspace.selectedId
    loading.value = true
    error.value = ''
    try {
      const result = await api.listWorkspaceChanges(folderId, controller.signal)
      if (controller.signal.aborted || folderId !== workspace.selectedId) return
      const previous = selected.value
      // Keep the current preview mounted while its replacement is being read.
      changes.value = result.changes.map((item) =>
        previous?.path === item.path ? { ...previous, status: item.status } : item,
      )
      gitAvailable.value = result.git
      changesTruncated.value = result.truncated
      if (!changes.value.some((item) => item.path === selectedPath.value))
        selectedPath.value = changes.value[0]?.path || ''
      if (selectedPath.value) await selectChange(selectedPath.value)
      else {
        diffController?.abort()
        diffController = undefined
        diffLoading.value = false
        diffError.value = ''
      }
    } catch (reason) {
      if (!controller.signal.aborted)
        error.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      if (listController === controller) {
        loading.value = false
        if (refreshAgain) {
          refreshAgain = false
          scheduleRefresh()
        }
      }
    }
  }

  function stop() {
    if (timer) clearTimeout(timer)
    timer = undefined
    refreshAgain = false
    listController?.abort()
    diffController?.abort()
    listController = diffController = undefined
    loading.value = diffLoading.value = false
  }

  // Observe product actions, not streaming transcript changes; tokens never query Git.
  const unsubscribe = run.$onAction(({ name, args, after }) => {
    if (!enabled.value) return
    let sessionId: string
    if (name === 'apply') {
      const [event] = args
      if (event.type === 'step_end') {
        const step = run.turns[event.sessionId]
          ?.find((turn) => turn.id === event.turnId)
          ?.steps.find((item) => item.id === event.stepId)
        if (step?.kind === 'read') return
      } else if (event.type !== 'file_change' && event.type !== 'turn_end') return
      sessionId = event.sessionId
    } else if (name === 'hydrateTask') sessionId = args[0]
    else if (name === 'setConnection' && args[1]) sessionId = args[0]
    else return
    const conversation = session.sessions.find((item) => item.id === sessionId)
    if (conversation && (conversation.folder_id || '') === workspace.selectedId)
      after(scheduleRefresh)
  })

  watch(
    () => [workspace.selectedId, enabled.value] as const,
    ([folderId, active], previous) => {
      stop()
      if (!previous || folderId !== previous[0]) {
        changes.value = []
        selectedPath.value = ''
        gitAvailable.value = true
        changesTruncated.value = false
        error.value = diffError.value = ''
      }
      if (active) void load()
    },
    { immediate: true, flush: 'sync' },
  )
  function refreshVisible() {
    if (document.visibilityState !== 'hidden') scheduleRefresh()
  }
  window.addEventListener('focus', refreshVisible)
  document.addEventListener('visibilitychange', refreshVisible)
  onActivated(() => {
    activated.value = true
  })
  onDeactivated(() => {
    activated.value = false
  })
  onScopeDispose(() => {
    stop()
    unsubscribe()
    window.removeEventListener('focus', refreshVisible)
    document.removeEventListener('visibilitychange', refreshVisible)
  })
  return {
    changes,
    selectedPath,
    selected,
    loading,
    error,
    gitAvailable,
    changesTruncated,
    diffLoading,
    diffError,
    load,
    selectChange,
  }
}
