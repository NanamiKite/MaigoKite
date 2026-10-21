import { t } from '@/i18n'
import { onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useInboxStore } from '@/stores/inbox'
import { useRunStore } from '@/stores/run'
import { useSettingsStore } from '@/stores/settings'
import { useWorkspaceStore } from '@/stores/workspace'
import { desktopEffects } from './useElectron'

export function useDesktopBridge() {
  const desktop = desktopEffects
  if (!desktop) return
  const session = useSessionStore(),
    inbox = useInboxStore(),
    run = useRunStore(),
    settings = useSettingsStore(),
    workspace = useWorkspaceStore(),
    router = useRouter()
  const mountedAt = Date.now()
  const notified = new Set<string>(),
    completed = new Set<string>()
  const cleanup: Array<() => void> = []
  const capsuleRequests: Array<{ sessionId: string; content: string; time: number }> = []
  watch(
    () => inbox.items.map((item) => item.id).join(','),
    () => {
      for (const item of inbox.items) {
        if (notified.has(item.id)) continue
        notified.add(item.id)
        if (!document.hasFocus())
          void desktop
            .notify({
              title: t('{0} · 等你', [settings.profile.assistantName]),
              body: item.title,
              sessionId: item.sessionId,
              approvalId: item.mode === 'approval' ? item.id : undefined,
            })
            .catch(() => undefined)
      }
    },
  )
  watch(
    () =>
      Object.values(run.turns)
        .flatMap((turns) =>
          turns
            .filter((t) => ['completed', 'failed', 'cancelled'].includes(t.status))
            .map((t) => `${t.sessionId}:${t.id}`),
        )
        .join(','),
    () => {
      for (const turns of Object.values(run.turns))
        for (const turn of turns) {
          if (!['completed', 'failed', 'cancelled'].includes(turn.status) || completed.has(turn.id))
            continue
          completed.add(turn.id)
          const capsule = capsuleRequests.findIndex(
            (request) =>
              request.sessionId === turn.sessionId &&
              request.content === turn.userText &&
              turn.endedAt &&
              turn.endedAt >= request.time,
          )
          if (capsule >= 0) capsuleRequests.splice(capsule, 1)
          if ((!document.hasFocus() || capsule >= 0) && turn.endedAt && turn.endedAt >= mountedAt)
            void desktop
              .notify({
                title: settings.profile.assistantName,
                body:
                  turn.status === 'completed'
                    ? t('任务已完成。')
                    : turn.status === 'failed'
                      ? t('任务失败。')
                      : t('任务已停止。'),
                sessionId: turn.sessionId,
              })
              .catch(() => undefined)
        }
    },
  )
  onMounted(async () => {
    const api = desktop
    if (!api) return
    cleanup.push(
      api.onCapsule(async (request) => {
        let queued: { sessionId: string; content: string; time: number } | undefined
        try {
          let id = request.newSession
            ? undefined
            : session.sessions.find(
                (item) => !item.archived && (item.folder_id || '') === workspace.selectedId,
              )?.id
          if (!id) id = (await session.createSession()).id
          queued = { sessionId: id, content: request.content, time: Date.now() }
          capsuleRequests.push(queued)
          await session.sendToSession(id, request.content)
        } catch (reason) {
          if (queued) {
            const index = capsuleRequests.indexOf(queued)
            if (index >= 0) capsuleRequests.splice(index, 1)
          }
          session.error = reason instanceof Error ? reason.message : String(reason)
          void api
            .notify({
              title: settings.profile.assistantName,
              body: t('速记未能发送，请打开主窗口重试。'),
            })
            .catch(() => undefined)
        }
      }),
    )
    cleanup.push(
      api.onNotification(async (value) => {
        if (value.allow && value.approvalId) {
          await inbox.refresh()
          const item = inbox.items.find((a) => a.id === value.approvalId)
          if (item) {
            try {
              await inbox.decide(item, 'allow')
            } catch {
              /* Inbox shows the failure. */
            }
          }
        } else if (value.sessionId) await router.push(`/chat/${value.sessionId}`)
      }),
    )
  })
  onUnmounted(() => cleanup.forEach((off) => off()))
}
