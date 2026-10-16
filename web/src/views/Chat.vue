<script setup lang="ts">
import { vDeskPaper } from '@/utils/desk-paper'
import { t } from '@/i18n'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useSettingsStore } from '@/stores/settings'
import { useUIStore } from '@/stores/ui'
import { useRunStore } from '@/stores/run'
import { useWorkspaceStore } from '@/stores/workspace'
import { useInboxStore } from '@/stores/inbox'
import type { AwaitItem, Turn } from '@/api/events'
import { useAutoFollow } from '@/composables/useAutoFollow'
import { useChatPosition } from '@/composables/useChatPosition'
import { presentationNodes, pendingItem, pendingOrder } from '@/utils/rail-presentation'
import { remainingMessages } from '@/stores/session-projection'
import { revealRailNode } from '@/utils/rail-navigation'
import { overlayPanel } from '@/utils/layout-media'
import { pickMygoQuote } from '@/config/mygo-quotes'
import { paperLight, takeWelcomeIntroduction } from '@/utils/welcome-presentation'
import { modelSwitches } from '@/utils/model-switches'
import { copySubagentSettings } from '@/config/subagents'
import { api, type ChatMessage, type PermissionMode } from '@/api/client'
import UserMessage from '@/components/chat-area/UserMessage.vue'
import GeneratedImages from '@/components/chat-area/GeneratedImages.vue'
import type { ImageReference } from '@/api/images'
import AgentMessage from '@/components/chat-area/AgentMessage.vue'
import MessageForkAction from '@/components/chat-area/MessageForkAction.vue'
import ModelSwitchNotice from '@/components/chat-area/ModelSwitchNotice.vue'
import RailTrack from '@/components/rail/RailTrack.vue'
import RailMinimap from '@/components/rail/RailMinimap.vue'
import InputBox from '@/components/chat-area/InputBox.vue'
import SubagentSettings from '@/components/chat-area/SubagentSettings.vue'
import StatPopover from '@/components/inkwell/StatPopover.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import WelcomeSheet from '@/components/chat-area/WelcomeSheet.vue'
import PendingReminder from '@/components/chat-area/PendingReminder.vue'
import QueuedMessages from '@/components/chat-area/QueuedMessages.vue'
import AppIcon from '@/components/common/AppIcon.vue'
import ReadingWidth from '@/components/chat-area/ReadingWidth.vue'
const route = useRoute(),
  router = useRouter(),
  session = useSessionStore(),
  settings = useSettingsStore(),
  ui = useUIStore(),
  run = useRunStore(),
  workspace = useWorkspaceStore()
const sessionId = computed(() => String(route.params.id || ''))
const inbox = useInboxStore()
const pending = computed(() =>
  inbox.items
    .filter((item) => item.sessionId === sessionId.value && pendingItem(item))
    .sort(pendingOrder),
)
const scroll = ref<HTMLElement | null>(null),
  paper = ref<HTMLElement | null>(null),
  composer = ref<InstanceType<typeof InputBox> | null>(null),
  visibleCount = ref(20),
  sending = ref(false),
  restoring = ref(false)
const highlightedTurn = ref('')
const mode = ref<PermissionMode>('workspace')
let draftPermissionChosen = false
watch(
  () => settings.permissions.default_mode,
  (value) => {
    if (!sessionId.value && !draftPermissionChosen) mode.value = value
  },
)
const draftModel = ref(''),
  draftThinking = ref(''),
  savingThinking = ref(false)
const draftSubagents = ref(copySubagentSettings(settings.subagentDefaults)),
  savingSubagents = ref(false)
let draftSubagentsChosen = false
watch(
  () => settings.subagentDefaults,
  (value) => {
    if (!sessionId.value && !draftSubagentsChosen)
      draftSubagents.value = copySubagentSettings(value)
  },
)
const draftMemory = ref(false),
  savingMemory = ref(false)
const memoryEnabled = computed(() =>
  sessionId.value ? session.activeSession?.memory_enabled === true : draftMemory.value,
)
watch(
  () => workspace.selectedId,
  () => {
    draftMemory.value = false
  },
)
const turns = computed(() => run.turns[sessionId.value] || [])
const modelChanges = computed<Map<string, string>>((previous) => {
  // Compare all loaded history before paging; streaming text leaves markers stable.
  const next = modelSwitches(turns.value)
  return previous &&
    previous.size === next.size &&
    [...next].every(([id, name]) => previous.get(id) === name)
    ? previous
    : next
})
const forkDisabled = computed(
  () =>
    sending.value ||
    restoring.value ||
    Boolean(session.runningTaskIds[sessionId.value]?.length) ||
    turns.value.some((turn) => ['running', 'waiting'].includes(turn.status)),
)
const savedMessages = computed(() =>
  sessionId.value ? session.messages[sessionId.value] || [] : [],
)
const unprojectedMessages = computed<ChatMessage[]>((previous) => {
  const next = remainingMessages(savedMessages.value, turns.value)
  return previous &&
    previous.length === next.length &&
    next.every((message, index) => message === previous[index])
    ? previous
    : next
})
type TimelineItem = {
  key: string
  time: number
  turn: Turn | undefined
  message: ChatMessage | undefined
}
const timeline = computed<TimelineItem[]>((previous) => {
  const entries = new Map<string, TimelineItem>(previous?.map((item) => [item.key, item]))
  const next: TimelineItem[] = [
    ...turns.value.map((turn) => ({
      key: 'turn-' + turn.id,
      time: turn.startedAt,
      turn,
      message: undefined,
    })),
    ...unprojectedMessages.value.map((message) => ({
      key: 'message-' + message.id,
      time: Date.parse(message.created_at),
      message,
      turn: undefined,
    })),
  ].map((item) => {
    const prior = entries.get(item.key)
    return prior &&
      prior.time === item.time &&
      prior.turn === item.turn &&
      prior.message === item.message
      ? prior
      : item
  })
  // A text delta changes content, never chronological order. Keep old entries
  // stable and sort only when history membership or timestamps actually change.
  const sameOrder =
    previous?.length === next.length &&
    next.every((item) => entries.get(item.key)?.time === item.time)
  if (previous && sameOrder) {
    const byKey = new Map(next.map((item) => [item.key, item]))
    const ordered = previous.map((item) => byKey.get(item.key)!)
    return ordered.every((item, index) => item === previous[index]) ? previous : ordered
  }
  return next.sort((a, b) => a.time - b.time)
})
const visibleTimeline = computed(() => timeline.value.slice(-visibleCount.value))
const nodes = computed<{ id: string; turnId: string }[]>((previous) => {
  // The minimap measures mounted history only; hidden pages have no real position.
  const next = visibleTimeline.value.flatMap(({ turn }) =>
    turn
      ? [
          ...presentationNodes(turn).map((node) => ({ id: node.id, turnId: turn.id })),
          ...(turn.summary ? [{ id: 'end', turnId: turn.id }] : []),
        ]
      : [],
  )
  return previous &&
    previous.length === next.length &&
    next.every(
      (node, index) => node.id === previous[index]?.id && node.turnId === previous[index]?.turnId,
    )
    ? previous
    : next
})
function loadEarlier() {
  visibleCount.value += 20
  for (const item of visibleTimeline.value)
    if (item.turn?.inputs?.some((input) => input.status !== 'consumed'))
      void session.refreshTaskDetails(sessionId.value, item.turn.id)
}
const welcomeQuote = ref('')
const firstWelcome = ref(false)
const welcomeSettled = ref(false)
const lightPeriod = ref(paperLight(new Date().getHours()))
// Route reuse includes newly created empty sessions. Typing and stream updates
// must not reroll a quote while the same welcome screen is visible.
watch(
  () => (timeline.value.length ? null : sessionId.value),
  (id) => {
    if (id !== null) {
      welcomeQuote.value = pickMygoQuote()
      firstWelcome.value = takeWelcomeIntroduction()
      welcomeSettled.value = false
      lightPeriod.value = paperLight(new Date().getHours())
    }
  },
  { immediate: true },
)
function finishWelcome(event: AnimationEvent) {
  // Child animations bubble too; only the workpaper's own end settles the scene.
  if (event.target === event.currentTarget) welcomeSettled.value = true
}
const lastMessage = computed(
  () => savedMessages.value.filter((m) => m.role === 'user').slice(-1)[0]?.content || '',
)
const queued = computed(() => session.queuedMessages[sessionId.value] || [])
const guideReason = computed(() => session.steeringReason(sessionId.value))
const uncertainInputs = computed(() =>
  (run.turns[sessionId.value] || []).flatMap((turn) => {
    const submission = session.inputSubmissions[turn.id]
    return submission?.uncertain ? [{ taskId: turn.id, ...submission }] : []
  }),
)
const interrupted = computed(() => session.interruptedTask(sessionId.value))
const submission = computed(() => session.submissions[sessionId.value])
const { following, onScroll, resume, pause } = useAutoFollow(scroll, () => {
  const latest = timeline.value[timeline.value.length - 1]
  const turn = latest?.turn
  return [
    sessionId.value,
    timeline.value.length,
    latest?.key,
    latest?.message?.content.length,
    turn?.text.length,
    turn?.thinking.length,
    turn?.timeline.length,
    turn?.steps[turn.steps.length - 1]?.status,
    turn?.status,
    turn && modelChanges.value.get(turn.id),
    turn?.inputs?.map((input) => `${input.id}:${input.status}`).join(','),
  ].join('|')
})
const greeting = computed(() => {
  const hour = new Date().getHours()
  return hour < 6
    ? t('夜深了')
    : hour < 11
      ? t('早上好')
      : hour < 14
        ? t('中午好')
        : hour < 18
          ? t('午后好')
          : t('晚上好')
})
const unfinished = computed(() =>
  session.sessions
    .filter((item) => !item.archived && (item.folder_id || '') === workspace.selectedId)
    .flatMap((item) => run.turns[item.id] || [])
    .filter((t) => ['waiting', 'failed', 'cancelled'].includes(t.status) && t.userText)
    .slice(-3),
)
let controls: Promise<unknown> = Promise.resolve()
let controlError = ''
let controlGeneration = 0
watch(
  sessionId,
  (id) => {
    controlGeneration++
    controlError = ''
    controls = Promise.resolve()
    highlightedTurn.value = ''
    if (ui.chatTurnRequest && ui.chatTurnRequest.sessionId !== id)
      ui.clearChatTurnRequest(ui.chatTurnRequest.serial)
    if (id) void session.switchSession(id)
    else {
      session.activeSessionId = null
      draftPermissionChosen = false
      draftMemory.value = false
      draftSubagentsChosen = false
      draftSubagents.value = copySubagentSettings(settings.subagentDefaults)
      mode.value = settings.permissions.default_mode
    }
    void nextTick(() => {
      if (
        !timeline.value.length &&
        (document.activeElement === document.body || document.activeElement?.closest('.new-page'))
      )
        composer.value?.focus()
    })
  },
  { immediate: true },
)
useChatPosition(sessionId, scroll, visibleCount, { following, resume, pause })
onMounted(() => {
  if (!settings.loaded) void settings.loadSettings()
  void settings.loadPermissionSettings()
  void api
    .getCapabilities()
    .then((available) => {
      session.capabilities = available
    })
    .catch(() => {})
})
async function send(
  content: string,
  intent: 'send' | 'guide' | 'later' = 'send',
  images: ImageReference[] = [],
) {
  if (sending.value) return
  sending.value = true
  let id = sessionId.value
  try {
    if (images.length && (intent !== 'send' || (id && session.runningTaskIds[id]?.length)))
      throw new Error(t('请等待当前任务结束后再发送图片。'))
    if (id && intent === 'guide') {
      await session.guideTask(id, content)
      resume()
      return
    }
    if (id && (intent === 'later' || (intent === 'send' && session.runningTaskIds[id]?.length))) {
      session.queueMessage(id, content)
      return
    }
    await controls
    if (controlError) throw new Error(controlError)
    if (!id) {
      const useMemory = draftMemory.value
      const item = await session.createSession('新对话', workspace.selectedId, draftSubagents.value)
      id = item.id
      await router.replace('/chat/' + id)
      await session.setPermissionMode(mode.value, id)
      if (useMemory) await session.manageSession(id, { memory_enabled: true })
      await session.setConversationRuntime(
        draftModel.value || settings.defaultModelId,
        draftThinking.value,
        id,
      )
    }
    await session.sendToSession(id, content, undefined, images)
    resume()
  } finally {
    sending.value = false
  }
}
async function sendQueuedNow(id: string) {
  await session.guideQueued(sessionId.value, id)
}
async function restore() {
  if (restoring.value) return
  restoring.value = true
  try {
    await session.manageSession(sessionId.value, { archived: false })
    session.showArchived = false
  } catch (reason) {
    session.error = reason instanceof Error ? reason.message : String(reason)
  } finally {
    restoring.value = false
  }
}
function configure(action: () => Promise<unknown>) {
  const generation = controlGeneration
  controls = controls
    .then(action)
    .then(() => {
      if (generation === controlGeneration) controlError = ''
    })
    .catch((reason) => {
      if (generation === controlGeneration) controlError = session.error = String(reason)
    })
}
function permission(value: PermissionMode) {
  const id = sessionId.value
  if (!id) {
    draftPermissionChosen = true
    mode.value = value
  } else
    configure(async () => {
      await session.setPermissionMode(value, id)
      if (sessionId.value === id) mode.value = session.activeSession?.permission_mode || 'workspace'
    })
}
function subagents(value: { calls: number; concurrent: number; modelIds?: string[] }) {
  const id = sessionId.value
  if (!id) {
    draftSubagentsChosen = true
    draftSubagents.value = {
      calls: value.calls,
      concurrent: value.concurrent,
      modelIds: value.modelIds ? [...value.modelIds] : [...draftSubagents.value.modelIds],
    }
    return
  }
  savingSubagents.value = true
  configure(async () => {
    try {
      await session.setSubagentLimits(value.calls, value.concurrent, id, value.modelIds)
    } finally {
      savingSubagents.value = false
    }
  })
}
function toggleMemory() {
  const enabled = !memoryEnabled.value
  const id = sessionId.value
  if (!id) {
    draftMemory.value = enabled
    return
  }
  savingMemory.value = true
  configure(async () => {
    try {
      await session.manageSession(id, { memory_enabled: enabled })
    } catch (reason) {
      // Refresh persisted state if the response was lost; never resend a toggle.
      await session.loadSessions()
      throw reason
    } finally {
      savingMemory.value = false
    }
  })
}
function model(value: string) {
  draftModel.value = value
  draftThinking.value = ''
  const id = sessionId.value
  if (!id) return
  savingThinking.value = true
  configure(async () => {
    try {
      await session.setConversationRuntime(value, '', id)
    } finally {
      savingThinking.value = false
    }
  })
}
function thinking(value: string) {
  draftThinking.value = value
  const id = sessionId.value
  if (!id) return
  savingThinking.value = true
  configure(async () => {
    try {
      await session.setConversationRuntime(
        session.sessions.find((item) => item.id === id)?.model_id || settings.defaultModelId,
        value,
        id,
      )
    } finally {
      savingThinking.value = false
    }
  })
}
async function confirmInput(taskId: string) {
  try {
    await session.confirmInputSubmission(sessionId.value, taskId)
  } catch {
    /* The original key and the inline error remain available. */
  }
}
async function jump(node: { id: string; turnId: string }) {
  const index = timeline.value.findIndex((item) => item.turn?.id === node.turnId)
  visibleCount.value = Math.max(visibleCount.value, timeline.value.length - index)
  await nextTick()
  revealRailNode(
    document.querySelector<HTMLElement>(
      '[data-rail-node="' + CSS.escape(node.turnId + ':' + node.id) + '"]',
    ),
  )
}
watch(
  () => {
    const request = ui.chatTurnRequest
    if (!request || request.sessionId !== sessionId.value) return null
    return timeline.value.some((item) => item.turn?.id === request.turnId) ? request.serial : null
  },
  async (serial, _previous, onCleanup) => {
    const request = ui.chatTurnRequest
    if (!serial || !request) return
    onCleanup(() => ui.clearChatTurnRequest(serial))
    const index = timeline.value.findIndex((item) => item.turn?.id === request.turnId)
    pause()
    highlightedTurn.value = ''
    visibleCount.value = Math.max(visibleCount.value, timeline.value.length - index)
    const closeDrawer = ui.drawerOpen && overlayPanel('drawer')
    if (closeDrawer) ui.drawerOpen = false
    await nextTick()
    // Let overlay focus restoration finish before focusing the destination.
    if (closeDrawer) await nextTick()
    if (ui.chatTurnRequest?.serial !== serial || sessionId.value !== request.sessionId) return
    const target = scroll.value?.querySelector<HTMLElement>(
      `[data-turn-id="${CSS.escape(request.turnId)}"]`,
    )
    if (!target) return
    revealRailNode(target, 'start')
    highlightedTurn.value = request.turnId
    ui.clearChatTurnRequest(serial)
  },
  { flush: 'post', immediate: true },
)
function clearTurnHighlight(id: string | undefined) {
  if (highlightedTurn.value === id) highlightedTurn.value = ''
}
async function locate(item: AwaitItem) {
  const index = timeline.value.findIndex((entry) => entry.turn?.id === item.turnId)
  if (index >= 0) visibleCount.value = Math.max(visibleCount.value, timeline.value.length - index)
  await nextTick()
  const anchor = scroll.value?.querySelector<HTMLElement>(
    `[data-await-anchor="${CSS.escape(item.id)}"]`,
  )
  const node = anchor?.querySelector<HTMLButtonElement>('.node-button')
  revealRailNode(node || null)
  if (node?.getAttribute('aria-expanded') === 'false') node.click()
  await nextTick()
  const card = scroll.value?.querySelector<HTMLElement>(`[data-await="${CSS.escape(item.id)}"]`)
  const control = card?.querySelector<HTMLElement>(
    '[data-await-action]:not(:disabled), textarea:not(:disabled)',
  )
  control?.focus({ preventScroll: true })
  control?.scrollIntoView({ block: 'center' })
}
watch(
  () => [route.query.await, sessionId.value, pending.value.map((item) => item.id).join('|')],
  () => {
    const id = typeof route.query.await === 'string' ? route.query.await : ''
    const item = pending.value.find((entry) => entry.id === id)
    if (item) void locate(item)
  },
  { flush: 'post', immediate: true },
)
</script>
<template>
  <div
    id="chat-page"
    class="chat-page inkline-preview"
    :class="{ empty: !timeline.length }"
  >
    <Teleport
      v-if="session.capabilities.subagents === true && !session.activeSession?.archived"
      to="#chat-session-actions"
    >
      <SubagentSettings
        :key="sessionId"
        :calls="sessionId ? (session.activeSession?.max_subagent_calls ?? 0) : draftSubagents.calls"
        :concurrent="
          sessionId
            ? (session.activeSession?.max_concurrent_subagents ?? 1)
            : draftSubagents.concurrent
        "
        :model-options="settings.modelProfiles"
        :model-ids="
          sessionId ? (session.activeSession?.subagent_model_ids ?? []) : draftSubagents.modelIds
        "
        :model-selection-supported="session.capabilities.subagent_model_selection === true"
        :saving="savingSubagents"
        :disabled="sending"
        @change="subagents"
      />
    </Teleport>
    <Teleport
      v-if="session.capabilities.scoped_memory === true && !session.activeSession?.archived"
      to="#chat-session-actions"
    >
      <StatPopover
        :label="$t('本对话记忆')"
        :title="memoryEnabled ? $t('本对话记忆：开启') : $t('本对话记忆：关闭')"
        placement="below"
        toolbar
      >
        <template #trigger>
          {{ $t('记忆') }}
        </template>
        <div class="conversation-memory">
          <button
            class="secondary-button"
            type="button"
            role="switch"
            :aria-checked="memoryEnabled"
            :disabled="sending || savingMemory"
            @click="toggleMemory"
          >
            {{ memoryEnabled ? $t('本对话记忆：开启') : $t('本对话记忆：关闭') }}
          </button>
          <p>{{ $t('只有本工作区和本对话都开启时才使用保存的记忆；不会读取其他工作区。') }}</p>
          <p>{{ $t('所选模型会收到这些记忆；关闭只影响后续请求，不会清除历史回复。') }}</p>
          <RouterLink to="/memory">
            {{ $t('管理工作区记忆') }}
          </RouterLink>
        </div>
      </StatPopover>
    </Teleport>
    <!-- Stable wrapper and one InputBox: a paper composition, not a second composer. -->
    <div
      ref="paper"
      v-desk-paper="!timeline.length"
      class="chat-body"
      :class="{ workpaper: !timeline.length, 'first-welcome': !timeline.length && firstWelcome }"
      :data-light="lightPeriod"
      :data-welcome-settled="welcomeSettled || undefined"
      @focusin="welcomeSettled = true"
      @animationend="finishWelcome"
    >
      <ReadingWidth :host="paper" />
      <div class="conversation">
        <div
          ref="scroll"
          class="message-list"
          @scroll.passive="onScroll"
        >
          <WelcomeSheet
            v-if="!turns.length && !unprojectedMessages.length"
            :key="'welcome-' + sessionId"
            class="welcome-state"
            :greeting="
              $t('{0}，{1}', [
                greeting,
                ui.appearance.greetingName === '你' ? $t('你') : ui.appearance.greetingName,
              ])
            "
            :quote="welcomeQuote"
            @draft="composer?.restoreDraft($event)"
          >
            <nav
              v-if="unfinished.length"
              class="unfinished"
            >
              <span>{{ $t('上次没做完的事') }}</span>
              <router-link
                v-for="turn in unfinished"
                :key="turn.id"
                :to="'/chat/' + turn.sessionId"
              >
                {{ turn.userText }}
              </router-link>
            </nav>
          </WelcomeSheet>
          <div
            v-else
            class="messages-inner"
          >
            <p
              v-if="session.activeSession?.forked_from_conversation_id"
              class="empty-copy"
              role="status"
            >
              {{ $t('这是独立分岔，工作区文件仍与原对话共享。') }}
            </p>
            <p
              v-if="session.historyTruncated[sessionId]"
              class="chat-error"
              role="status"
            >
              {{ $t('较早的记录未完整加载。') }}
            </p>
            <button
              v-if="timeline.length > visibleCount"
              class="earlier"
              @click="loadEarlier"
            >
              {{ $t('再看前面的内容') }}
            </button>
            <section
              v-for="item in visibleTimeline"
              :key="item.key"
              class="turn"
              :class="{ located: item.turn?.id === highlightedTurn }"
              :data-turn-id="item.turn?.id"
              :tabindex="item.turn ? -1 : undefined"
              @animationend.self="clearTurnHighlight(item.turn?.id)"
            >
              <template v-if="item.message">
                <UserMessage
                  v-if="item.message.role === 'user'"
                  :content="item.message.content"
                  :attachments-json="item.message.attachments_json"
                />
                <AgentMessage
                  v-else
                  :content="item.message.content"
                  :assistant-name="settings.profile.assistantName"
                >
                  <template #actions>
                    <MessageForkAction
                      v-if="!item.message.streaming"
                      :key="item.message.id"
                      :conversation-id="sessionId"
                      :message-id="item.message.id"
                      :disabled="forkDisabled"
                    />
                  </template>
                </AgentMessage>
              </template>
              <template v-else-if="item.turn">
                <ModelSwitchNotice
                  v-if="item.turn.userText && modelChanges.has(item.turn.id)"
                  :model="modelChanges.get(item.turn.id) || ''"
                />
                <UserMessage
                  v-if="item.turn.userText"
                  :content="item.turn.userText"
                  :attachments-json="item.turn.attachmentsJSON"
                />
                <RailTrack
                  :turn="item.turn"
                  :can-resend="!session.isSending && !session.activeSession?.archived && !sending"
                  :resend-input="(id) => session.resendInput(sessionId, item.turn!.id, id)"
                >
                  <GeneratedImages :steps="item.turn.steps" />
                  <AgentMessage
                    v-if="item.turn.text"
                    :content="item.turn.text"
                    :assistant-name="settings.profile.assistantName"
                    :is-streaming="item.turn.status === 'running'"
                  >
                    <template #actions>
                      <MessageForkAction
                        :key="item.turn.messageId"
                        :conversation-id="sessionId"
                        :message-id="item.turn.messageId"
                        :disabled="forkDisabled"
                      />
                    </template>
                  </AgentMessage>
                  <InkLoader v-else-if="item.turn.status === 'running'" />
                </RailTrack>
                <p
                  v-if="item.turn.inputsError"
                  class="chat-error"
                  role="alert"
                >
                  {{ item.turn.inputsError }}
                  <button
                    class="text-button"
                    @click="session.refreshTaskDetails(sessionId, item.turn.id)"
                  >
                    {{ $t('重试') }}
                  </button>
                </p>
              </template>
            </section>
          </div>
        </div>
        <RailMinimap
          :nodes="nodes"
          :scroll-root="scroll"
          @jump="jump"
        />
        <button
          v-if="!following"
          class="follow-button"
          @click="resume"
        >
          ↓ {{ session.isSending ? $t('查看最新进展') : $t('回到最新消息') }}
        </button>
      </div>
      <p
        v-if="session.error"
        class="chat-error"
        role="alert"
      >
        {{ session.error }}
      </p>
      <div
        v-if="submission?.uncertain"
        class="queued-messages"
        role="status"
      >
        <p>{{ $t('尚未确认提交：{0}', [submission.content]) }}</p>
        <button @click="session.confirmSubmission(sessionId)">
          {{ $t('确认提交结果') }}
        </button>
      </div>
      <div
        v-if="interrupted"
        class="queued-messages"
        role="status"
      >
        <p>{{ $t('任务已中断，未自动重试。') }}</p>
        <template v-if="session.capabilities.task_resume">
          <button
            :disabled="
              !session.canResumeTask(sessionId) || session.resumingTaskIds.includes(interrupted.id)
            "
            @click="session.resumeInterruptedTask(sessionId)"
          >
            {{
              session.resumingTaskIds.includes(interrupted.id)
                ? $t('正在确认恢复…')
                : $t('继续任务')
            }}
          </button>
          <p>{{ $t('仅可继续文件读写任务。') }}</p>
        </template>
      </div>
      <div
        v-for="input in uncertainInputs"
        :key="input.key"
        class="queued-messages"
        role="status"
      >
        <p>{{ $t('尚未确认补充：{0}', [input.content]) }}</p>
        <button
          class="text-button"
          @click="confirmInput(input.taskId)"
        >
          {{ $t('确认提交结果') }}
        </button>
      </div>
      <p
        v-if="session.inputErrors[sessionId] && uncertainInputs.length"
        class="chat-error"
        role="alert"
      >
        {{ session.inputErrors[sessionId] }}
      </p>
      <QueuedMessages
        :key="sessionId"
        :items="queued"
        :paused="Boolean(session.queuedPaused[sessionId])"
        :guide-reason="guideReason"
        :can-continue="!session.isSending && !submission && !session.activeSession?.archived"
        :guide="sendQueuedNow"
        @edit="(id, content) => session.editQueued(sessionId, id, content)"
        @remove="session.removeQueued(sessionId, $event)"
        @reorder="(id, target, position) => session.reorderQueued(sessionId, id, target, position)"
        @continue="session.resumeQueued(sessionId)"
      />
      <div
        v-if="session.activeSession?.archived"
        class="archived-notice"
      >
        <span>{{ $t('此对话已归档，可查看历史记录。') }}</span>
        <button
          class="secondary-button"
          :disabled="restoring"
          :aria-busy="restoring || undefined"
          @click="restore"
        >
          {{ restoring ? $t('正在恢复…') : $t('恢复对话') }}
        </button>
      </div>
      <PendingReminder
        :items="pending"
        :scroll-root="scroll"
        @locate="locate"
      />
      <InputBox
        v-if="!session.activeSession?.archived"
        ref="composer"
        :session-id="sessionId"
        :waiting="pending.length > 0"
        :queued-count="queued.length"
        :guide-reason="guideReason"
        :submit="send"
        :assistant-name="settings.profile.assistantName"
        :is-sending="session.isSending"
        :is-submitting="sending"
        :is-stopping="session.isStopping"
        :model-id="
          sessionId
            ? session.activeSession?.model_id || settings.defaultModelId
            : draftModel || settings.defaultModelId
        "
        :model-options="settings.modelProfiles"
        :thinking="sessionId ? session.activeSession?.thinking || '' : draftThinking"
        :saving-thinking="savingThinking"
        :permission-mode="session.activeSession?.permission_mode || mode"
        :last-message="lastMessage"
        @send="send"
        @cancel="session.cancelActiveTask"
        @permission-mode-change="permission"
        @model-change="model"
        @thinking-change="thinking"
      />
      <footer
        v-if="!timeline.length"
        class="workpaper-signature"
      >
        <span>
          <AppIcon :size="24" />
          {{ settings.profile.assistantName }}
        </span>
        <router-link to="/overview">
          {{ $t('工作记录') }}
          <span aria-hidden="true">↗</span>
        </router-link>
      </footer>
    </div>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/chat';
.conversation-memory {
  display: grid;
  gap: var(--space-3);
  max-width: 24rem;
  font-size: var(--type-small);
  p {
    margin: 0;
  }
}
</style>
