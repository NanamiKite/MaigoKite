<script setup lang="ts">
import { computed } from 'vue'
import type { SubagentRun } from '@/api/subagent-events'
import type { Turn } from '@/api/events'
import AgentMessage from '@/components/chat-area/AgentMessage.vue'
import ThinkingPanel from '@/components/rail/ThinkingPanel.vue'
import Pulse from '@/components/shell/Pulse.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import RunOperation from './RunOperation.vue'
const props = defineProps<{ child: SubagentRun; turn: Turn }>()
const live = computed(
  () =>
    ['running', 'waiting'].includes(props.turn.status) &&
    ['queued', 'running'].includes(props.child.status),
)
const status = computed(() =>
  !live.value && ['queued', 'running'].includes(props.child.status)
    ? '已中断'
    : {
        queued: '排队中',
        running: '正在做',
        completed: '已完成',
        failed: '失败',
        cancelled: '已停止',
      }[props.child.status],
)
const entries = computed(() => {
  const messages = new Map(props.child.messages.map((item) => [item.id, item]))
  const steps = new Map(props.child.steps.map((item) => [item.id, item]))
  return props.child.timeline.map((item) => ({
    ...item,
    message: item.kind === 'message' ? messages.get(item.id) : undefined,
    step: item.kind === 'step' ? steps.get(item.id) : undefined,
  }))
})
const hasFinal = computed(() =>
  props.child.messages.some((message) => message.final && message.content),
)
</script>
<template>
  <section class="subagent-conversation" :aria-label="child.label || $t('子 Agent 对话')">
    <header>
      <strong>{{ child.label || $t('子 Agent') }}</strong>
      <span role="status"
        ><Pulse :state="live ? 'acting' : child.status === 'failed' ? 'error' : 'idle'" />{{
          $t(status)
        }}</span
      >
    </header>
    <p v-if="turn.userText" class="parent-label" :title="turn.userText">
      {{ $t('来自运行：{0}', [turn.userText.split('\n')[0]]) }}
    </p>
    <p v-if="child.model || child.provider" class="model-label">
      {{ [child.model, child.provider].filter(Boolean).join(' · ') }}
    </p>
    <section class="delegated-task">
      <h3>{{ $t('委派任务') }}</h3>
      <p>{{ child.task || $t('这条记录未保存委派内容。') }}</p>
    </section>
    <template v-for="entry in entries" :key="entry.kind + ':' + entry.id">
      <template v-if="entry.message">
        <ThinkingPanel
          v-if="entry.message.reasoning"
          :text="entry.message.reasoning"
          status="completed"
          thinking-status="completed"
        />
        <AgentMessage
          v-if="entry.message.content"
          :content="entry.message.content"
          :assistant-name="child.label || $t('子 Agent')"
          :timestamp="entry.message.time ? new Date(entry.message.time) : undefined"
        />
        <p v-if="entry.message.truncated" class="record-note">
          {{ $t('内容较长，仅显示已保存的部分。') }}
        </p>
      </template>
      <RunOperation v-else-if="entry.step" :step="entry.step" :ended="!live" />
    </template>
    <p v-if="!live && !entries.length" class="record-note">{{ $t('这次运行未保存完整过程。') }}</p>
    <AgentMessage
      v-if="child.summary && !hasFinal && child.status === 'completed'"
      :content="child.summary"
      :assistant-name="child.label || $t('子 Agent')"
    />
    <p v-else-if="child.summary && child.status === 'failed'" role="alert" class="failure">
      {{ child.summary }}
    </p>
    <InkLoader v-if="live" />
    <p v-if="live" class="record-note" role="status">
      {{ $t(child.status === 'queued' ? '等待开始…' : '正在执行，回复和操作结果会显示在这里。') }}
    </p>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.subagent-conversation {
  min-width: 0;
  overflow-wrap: anywhere;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}
header strong {
  font-size: var(--text-md);
  font-weight: var(--weight-medium);
}
header span {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--ink-2);
}
.model-label,
.record-note {
  color: var(--ink-2);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}
.parent-label {
  @include mixins.ellipsis;
  color: var(--ink-2);
  font-size: var(--text-xs);
}
.delegated-task {
  margin-block: var(--space-5);
  padding-inline-start: var(--space-4);
  border-inline-start: var(--focus-width) solid var(--accent);
}
h3 {
  margin: 0;
  color: var(--ink-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
}
.delegated-task p {
  margin-bottom: 0;
  white-space: pre-wrap;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}
.failure {
  color: var(--seal);
  font-size: var(--text-sm);
}
:deep(.thinking-panel) {
  margin-inline-start: 0;
}
</style>
