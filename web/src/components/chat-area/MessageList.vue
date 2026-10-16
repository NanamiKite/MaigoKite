<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useSessionStore } from '@/stores/session'
import type { AgentActivityGroup, ChatMessage } from '@/api/client'
import IconSymbol from '@/components/common/IconSymbol.vue'
import UserMessage from './UserMessage.vue'
import AgentMessage from './AgentMessage.vue'
import AgentActivityPanel from './AgentActivityPanel.vue'
import ApprovalCard from './ApprovalCard.vue'

const props = withDefaults(defineProps<{ sessionId: string; assistantName?: string }>(), {
  assistantName: '助手',
})
const emit = defineEmits<{ (event: 'suggest', content: string): void }>()
const sessionStore = useSessionStore()
const listElement = ref<HTMLElement | null>(null)

const activityGroupByTask = computed(
  () => new Map(sessionStore.activeActivityGroups.map((group) => [group.task_id, group])),
)

function messageTaskId(message: ChatMessage): string {
  return message.role === 'assistant' && message.id.startsWith('stream-')
    ? message.id.slice('stream-'.length)
    : ''
}

function activityGroupForMessage(message: ChatMessage): AgentActivityGroup | undefined {
  return activityGroupByTask.value.get(messageTaskId(message))
}

const anchoredTaskIds = computed(
  () => new Set(sessionStore.activeMessages.map(messageTaskId).filter(Boolean)),
)
const unanchoredActivityGroups = computed(() =>
  sessionStore.activeActivityGroups.filter((group) => !anchoredTaskIds.value.has(group.task_id)),
)

let scrollFrame = 0

async function scrollToLatest() {
  await nextTick()
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
  scrollFrame = requestAnimationFrame(() => {
    if (listElement.value) listElement.value.scrollTop = listElement.value.scrollHeight
    scrollFrame = 0
  })
}

watch(
  () => {
    const list = sessionStore.activeMessages
    const last = list[list.length - 1]
    const activities = sessionStore.activeActivityGroups
    const lastGroup = activities[activities.length - 1]
    const lastActivity = lastGroup?.items[lastGroup.items.length - 1]
    return [
      list.length,
      last?.id || '',
      last?.content.length || 0,
      activities.length,
      lastGroup?.status || '',
      lastActivity?.status || '',
      lastActivity?.summary?.length || 0,
      sessionStore.activeApprovals.length,
    ].join(':')
  },
  scrollToLatest,
  { flush: 'post' },
)
onMounted(scrollToLatest)
onUnmounted(() => {
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
})

const suggestions = [
  {
    icon: 'review',
    title: '整理一段内容',
    prompt: '我会粘贴一段内容，请帮我提取重点和下一步行动。',
  },
  {
    icon: 'search',
    title: '分析一个问题',
    prompt: '请帮我分析这个问题的核心矛盾、风险和可行方案。',
  },
  {
    icon: 'sparkles',
    title: '制定计划',
    prompt: '我有一个想法，请提问确认需求，并列出可执行计划。',
  },
]
</script>

<template>
  <div
    ref="listElement"
    class="message-list"
    :aria-label="$t('对话 {0}', [props.sessionId])"
  >
    <div
      v-if="sessionStore.activeMessages.length === 0"
      class="welcome-state"
    >
      <div class="welcome-orb">
        <IconSymbol
          name="sparkles"
          :size="27"
          :stroke-width="1.55"
        />
      </div>
      <p class="welcome-eyebrow">
        {{ props.assistantName }}
      </p>
      <h2>{{ $t('新对话') }}</h2>
      <div class="suggestions">
        <button
          v-for="item in suggestions"
          :key="item.title"
          type="button"
          @click="emit('suggest', item.prompt)"
        >
          <IconSymbol
            :name="item.icon"
            :size="17"
          />
          <span>{{ $t(item.title) }}</span>
          <IconSymbol
            name="chevron"
            :size="14"
          />
        </button>
      </div>
    </div>

    <div
      v-else
      class="messages-inner"
    >
      <template
        v-for="message in sessionStore.activeMessages"
        :key="message.id"
      >
        <AgentActivityPanel
          v-if="activityGroupForMessage(message)"
          :group="activityGroupForMessage(message)!"
        />
        <UserMessage
          v-if="message.role === 'user'"
          :content="message.content"
          :timestamp="new Date(message.created_at)"
        />
        <AgentMessage
          v-else
          :content="message.content"
          :assistant-name="props.assistantName"
          :timestamp="new Date(message.created_at)"
          :is-streaming="message.streaming"
        />
      </template>
      <AgentActivityPanel
        v-for="group in unanchoredActivityGroups"
        :key="group.task_id"
        :group="group"
      />
      <ApprovalCard
        v-for="approval in sessionStore.activeApprovals"
        :key="approval.id"
        :approval="approval"
      />
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.message-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.messages-inner {
  width: min(var(--measure-780px), 100%);
  margin: 0 auto;
  padding: var(--size-28) clamp(var(--space-6), 4vw, var(--space-9)) var(--space-6);
}
.welcome-state {
  display: flex;
  width: min(var(--measure-620px), 100%);
  min-height: 100%;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  margin: 0 auto;
  padding: var(--rail-node-height) var(--size-28) var(--size-28);
  text-align: center;
}
.welcome-orb {
  display: grid;
  width: var(--space-10);
  height: var(--space-10);
  margin-bottom: var(--size-18);
  place-items: center;
  border: var(--hairline) solid var(--paper-3);
  border-radius: var(--radius-lg);
  color: var(--on-accent);
  background: var(--paper-2);
  box-shadow: var(--shadow-card);
}
.welcome-eyebrow {
  margin: 0 0 var(--measure-7px);
  @include mixins.text-ink;
  font-size: var(--text-xs);
  font-weight: var(--weight-bold);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
}
.welcome-state h2 {
  margin: 0;
  font-size: clamp(var(--measure-25px), 4vw, var(--measure-34px));
  font-weight: var(--weight-bold);
  letter-spacing: var(--tracking-poster);
}
.welcome-copy {
  max-width: var(--measure-450px);
  margin: var(--size-10) 0 var(--space-7);
  color: var(--ink-2);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}
.suggestions {
  display: grid;
  width: min(var(--measure-500px), 100%);
  gap: var(--space-3);
}
.suggestions button {
  display: flex;
  width: 100%;
  min-height: var(--measure-46px);
  align-items: center;
  gap: var(--measure-11px);
  padding: 0 var(--size-14);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-lg);
  color: var(--ink-2);
  background: var(--paper-2);
  box-shadow: var(--shadow-card);
  cursor: pointer;
  text-align: left;
  transition:
    transform var(--motion-fast) var(--ease),
    opacity var(--motion-fast) var(--ease);
}
.suggestions button:hover {
  color: var(--ink);
  background: var(--paper);
  box-shadow: var(--shadow-card);
  transform: translateY(calc(-1 * var(--hairline)));
}
.suggestions button span {
  flex: 1;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
}
</style>
