<script setup lang="ts">
import type { Step } from '@/api/events'
import { formatDuration } from '@/utils/format'
import CodeBlock from '@/components/common/CodeBlock.vue'
defineProps<{ steps: Step[]; ended?: boolean }>()
const statusLabels = { pending: '等待执行', running: '进行中', completed: '完成', failed: '失败' }
function argumentsText(input: unknown) {
  return JSON.stringify(input, null, 2)?.slice(0, 20_000) || '{}'
}
</script>
<template>
  <div class="node-details">
    <article
      v-for="step in steps"
      :key="step.id"
    >
      <header>
        <span>{{ step.title }}</span>
        <small>
          {{
            $t(
              ended && ['running', 'pending'].includes(step.status)
                ? '未记录结束状态'
                : statusLabels[step.status],
            )
          }}
          · {{ formatDuration(step.durationMs) }}
          <template v-if="step.exitCode !== undefined">
            {{ $t('· 退出码 {0}', [step.exitCode]) }}
          </template>
        </small>
      </header>
      <details>
        <summary>{{ $t('参数') }}</summary>
        <p v-if="step.errorCode">
          {{ $t('错误代码：') }}
          <code>{{ step.errorCode }}</code>
        </p>
        <CodeBlock
          :content="argumentsText(step.input)"
          language="JSON"
        />
      </details>
      <CodeBlock
        v-if="step.output"
        :content="step.output"
        :kind="
          ['shell', 'bash', 'exec_command', 'powershell'].includes(step.tool) ? 'terminal' : 'code'
        "
        :title="$t('输出')"
      />
      <p
        v-if="step.hint || step.action"
        class="hint"
        role="status"
      >
        {{ step.hint }}
        <router-link
          v-if="step.action"
          :to="step.action.to"
        >
          {{ step.action.label }}
        </router-link>
      </p>
    </article>
  </div>
</template>
<style scoped lang="scss">
.node-details {
  margin-block: var(--space-2) var(--space-5);
  padding: var(--space-2) var(--space-4);
  border-inline-start: var(--hairline) solid var(--rule);
  color: var(--ink-2);
  font-size: var(--text-sm);
}
article + article {
  margin-top: var(--space-5);
}
header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: var(--space-3);
  margin-block: var(--space-2);
}
small {
  color: var(--ink-2);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}
summary {
  cursor: pointer;
  color: var(--ink-2);
  font-size: var(--text-xs);
  padding-block: var(--space-2);
}
.hint {
  color: var(--ink-2);
  border-inline-start: var(--focus-width) solid var(--wait);
  padding-inline-start: var(--space-3);
}
.hint a {
  margin-inline-start: var(--space-3);
  color: var(--accent);
}
</style>
