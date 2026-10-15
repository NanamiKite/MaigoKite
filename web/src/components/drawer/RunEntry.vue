<script setup lang="ts">
import { computed, ref } from 'vue'
import { t } from '@/i18n'
import type { AgentTask } from '@/api/client'
import type { Turn } from '@/api/events'
import type { SubagentRun } from '@/api/subagent-events'
import Receipt from '@/components/rail/Receipt.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import Pulse from '@/components/shell/Pulse.vue'
import RunOperation from './RunOperation.vue'
const props = defineProps<{
  task: AgentTask
  turn?: Turn
  active?: boolean
  stopping?: boolean
  operation?: string
  label: string
}>()
const emit = defineEmits<{ stop: []; openSubagent: [turnId: string, childId: string] }>()
const expanded = ref(Boolean(props.active))
const title = computed(() => props.task.title || props.task.input_text.split('\n')[0] || t('任务'))
const children = computed(() => props.turn?.subagents || [])
// Delegations get their own conversation links; all other operations retain details.
const operations = computed(
  () =>
    props.turn?.steps.filter(
      (step) => step.tool !== 'delegate' || !children.value.some((child) => child.id === step.id),
    ) || [],
)
const pulse = computed(() =>
  props.task.status === 'waiting_approval'
    ? 'waiting'
    : props.active
      ? 'acting'
      : props.turn?.status === 'failed'
        ? 'error'
        : 'idle',
)
function childStatus(child: SubagentRun) {
  if (!props.active && ['queued', 'running'].includes(child.status)) return '已中断'
  return {
    queued: '排队中',
    running: '正在做',
    completed: '已完成',
    failed: '失败',
    cancelled: '已停止',
  }[child.status]
}
</script>
<template>
  <section class="run-entry" :class="{ active }">
    <div class="entry-heading">
      <button
        type="button"
        class="entry-toggle"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        <Pulse :state="pulse" />
        <span :title="title">{{ title }}</span>
        <small>{{ label }}</small>
        <IconSymbol name="chevron" :size="12" :class="{ expanded }" />
      </button>
      <button
        v-if="active"
        type="button"
        class="text-button"
        :disabled="stopping"
        @click="emit('stop')"
      >
        {{ $t(stopping ? '请求停止中…' : '停止') }}
      </button>
    </div>
    <div v-if="expanded" class="entry-content">
      <p v-if="task.input_text" class="entry-request">{{ task.input_text }}</p>
      <p v-if="active && operation" class="entry-operation" role="status">{{ operation }}</p>
      <time :datetime="task.updated_at">{{
        new Date(task.updated_at).toLocaleString($i18n.locale)
      }}</time>
      <Receipt v-if="turn" :turn="turn" embedded />
      <p v-else class="empty-copy">{{ $t('运行明细尚未载入。') }}</p>
      <div v-if="operations.length" class="operation-list">
        <h4>{{ $t('操作记录') }}</h4>
        <RunOperation v-for="step in operations" :key="step.id" :step="step" :ended="!active" />
      </div>
      <div v-if="children.length" class="child-list">
        <h4>{{ $t('子 Agent') }}</h4>
        <button
          v-for="child in children"
          :key="child.id"
          type="button"
          class="child-link"
          :aria-label="$t('查看子 Agent 对话：{0}', [child.label || child.task || $t('子 Agent')])"
          @click="emit('openSubagent', task.id, child.id)"
        >
          <Pulse
            :state="
              child.status === 'running' && active
                ? 'acting'
                : child.status === 'failed'
                  ? 'error'
                  : 'idle'
            "
          />
          <span class="child-copy"
            ><strong>{{ child.label || $t('子 Agent') }}</strong
            ><small>{{ child.task || child.summary }}</small></span
          >
          <small class="child-status">{{ $t(childStatus(child)) }}</small>
          <IconSymbol name="chevron" :size="12" />
        </button>
      </div>
      <p v-if="task.last_error" class="entry-error" role="alert">{{ task.last_error }}</p>
    </div>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.run-entry {
  border-bottom: var(--hairline) solid var(--rule);
  padding-block: var(--space-2);
}
.entry-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.entry-toggle,
.child-link {
  @include mixins.interactive-row;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  width: 100%;
  padding: var(--space-3) var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--transparent);
  color: var(--ink);
  text-align: start;
  font-size: var(--text-sm);
  svg {
    flex-shrink: 0;
  }
}
.entry-toggle span {
  @include mixins.ellipsis;
  flex: 1;
}
.entry-toggle small {
  flex-shrink: 0;
  color: var(--ink-2);
  font-size: var(--text-xs);
}
.expanded {
  transform: rotate(var(--measure-90deg));
}
.text-button {
  @include mixins.text-action;
  flex-shrink: 0;
}
.entry-content {
  padding: var(--space-2) var(--space-2) var(--space-4);
  min-width: 0;
}
.entry-request {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}
.entry-operation {
  color: var(--accent);
  font-size: var(--text-xs);
}
time {
  font-size: var(--text-xs);
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
h4 {
  margin: var(--space-4) 0 var(--space-2);
  color: var(--ink-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
}
.child-copy {
  flex: 1;
  min-width: 0;
}
.child-link strong,
.child-link small {
  display: block;
}
.child-link strong {
  font-weight: var(--weight-medium);
}
.child-copy small {
  @include mixins.ellipsis;
  margin-top: var(--space-1);
  color: var(--ink-2);
  font-size: var(--text-xs);
}
.child-status {
  flex-shrink: 0;
  color: var(--ink-2);
  font-size: var(--text-xs);
}
.entry-error {
  color: var(--seal);
  overflow-wrap: anywhere;
  font-size: var(--text-sm);
}
</style>
