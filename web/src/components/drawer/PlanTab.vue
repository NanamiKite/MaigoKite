<script setup lang="ts">
import { computed } from 'vue'
import type { PlanItem } from '@/api/events'
import { useRunStore } from '@/stores/run'
import { useSessionStore } from '@/stores/session'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'
const run = useRunStore(),
  session = useSessionStore()
const turn = computed(() => run.turns[session.activeSessionId || '']?.slice(-1)[0])
const items = computed(() => turn.value?.plan || [])
// Count each step's reported status, not the task/tool completion state.
const completed = computed(() => items.value.filter((item) => item.status === 'completed').length)
const progress = computed(() => (items.value.length ? completed.value / items.value.length : 0))
const statusLabels: Record<PlanItem['status'], string> = {
  pending: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}
</script>
<template>
  <section class="plan-tab">
    <p
      v-if="turn?.planError"
      class="empty-copy"
      role="alert"
    >
      {{ turn.planError }}
      <button
        type="button"
        class="text-button"
        @click="session.refreshTaskDetails(session.activeSessionId!, turn.id)"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <InkLoader v-else-if="turn?.planLoading && !items.length" />
    <div
      v-else-if="!items.length"
      class="plan-empty"
    >
      <IconSymbol
        name="check"
        :size="24"
      />
      <strong>{{ $t('还没有执行计划。') }}</strong>
      <p>{{ $t('任务需要分步进行时，Agent 的计划会显示在这里。') }}</p>
    </div>
    <template v-else>
      <p
        class="plan-count"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {{ $t('已完成 {0} / {1} 步', [completed, items.length]) }}
      </p>
      <div
        class="plan-progress"
        role="progressbar"
        :aria-label="$t('计划进度')"
        :aria-valuenow="completed"
        :aria-valuemin="0"
        :aria-valuemax="items.length"
        :aria-valuetext="$t('已完成 {0} / {1} 步', [completed, items.length])"
      >
        <i :style="{ transform: `scaleX(${progress})` }" />
      </div>
      <ol
        class="plan-steps"
        role="list"
      >
        <li
          v-for="(item, index) in items"
          :key="`${turn?.id}:${index}`"
          class="plan-step"
          :class="item.status"
          :aria-current="item.status === 'in_progress' ? 'step' : undefined"
        >
          <svg
            class="plan-marker"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle
              cx="12"
              cy="12"
              r="9"
            />
            <path
              v-if="item.status === 'completed'"
              d="m7.5 12 3 3 6-6"
            />
            <circle
              v-else-if="item.status === 'in_progress'"
              class="plan-dot"
              cx="12"
              cy="12"
              r="3"
            />
          </svg>
          <span class="plan-content">{{ item.content }}</span>
          <span class="plan-status">{{ $t(statusLabels[item.status]) }}</span>
        </li>
      </ol>
    </template>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.text-button {
  @include mixins.text-action;
}
.plan-tab {
  padding: var(--space-6);
}
.plan-progress {
  height: var(--space-1);
  background: var(--rule);
  overflow: hidden;
  border-radius: var(--radius-pill);
}
.plan-progress i {
  display: block;
  height: 100%;
  background: var(--accent);
  transform-origin: left;
  transition: transform var(--duration) var(--ease);
}
.plan-steps {
  display: grid;
  gap: var(--space-2);
  list-style: none;
  padding: 0;
  margin: var(--space-4) 0 0;
}
.plan-step {
  display: grid;
  grid-template-columns: var(--size-18) minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--space-3);
  padding: var(--space-3);
  border-inline-start: var(--focus-width) solid var(--transparent);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  line-height: var(--leading-small);
  color: var(--ink-2);
}
.plan-marker {
  width: var(--size-18);
  height: var(--size-18);
  margin-top: var(--hairline);
  stroke: currentColor;
  stroke-width: var(--rail-glyph-stroke);
  stroke-linecap: round;
  stroke-linejoin: round;
}
.plan-dot {
  fill: currentColor;
  stroke: none;
}
.plan-content {
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.plan-status {
  font-size: var(--text-xs);
  white-space: nowrap;
}
.plan-step.completed {
  @include mixins.text-ink;
}
.plan-step.in_progress {
  color: var(--ink);
  background: var(--accent-soft);
  border-inline-start-color: var(--accent);
  font-weight: var(--weight-medium);
}
.in_progress .plan-status {
  @include mixins.text-ink;
}
.completed .plan-marker,
.in_progress .plan-marker {
  color: var(--accent);
}
.plan-empty {
  padding-block: var(--space-8);
  color: var(--ink-2);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}
.plan-empty strong {
  display: block;
  margin-top: var(--space-4);
  font-weight: var(--weight-medium);
}
.plan-count {
  font-size: var(--text-xs);
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
@media (prefers-reduced-motion: reduce) {
  .plan-progress i {
    transition: none;
  }
}
</style>
