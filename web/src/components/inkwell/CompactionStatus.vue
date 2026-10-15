<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/i18n'
import type { ContextCompaction, Turn } from '@/api/events'
import IconSymbol from '@/components/common/IconSymbol.vue'
import Pulse from '@/components/shell/Pulse.vue'

const props = withDefaults(
  defineProps<{
    compaction: ContextCompaction
    turnStatus: Turn['status']
    compact?: boolean
    announce?: boolean
  }>(),
  { compact: false, announce: false },
)
// A terminal task without a completion receipt is not successful compaction.
const interrupted = computed(
  () => props.compaction.status === 'running' && props.turnStatus !== 'running',
)
const running = computed(() => props.compaction.status === 'running' && !interrupted.value)
const labels = {
  running: '正在压缩上下文',
  completed: '上下文已压缩',
  failed: '上下文压缩失败',
  skipped: '未进行压缩',
}
const label = computed(() =>
  t(
    interrupted.value
      ? '压缩结果未确认'
      : props.compaction.reason === 'fixed_overhead_exceeded'
        ? '模型输入预算不足'
        : labels[props.compaction.status],
  ),
)
const detail = computed(() => {
  if (interrupted.value) return t('无法确认上下文是否已压缩。')
  if (props.compaction.status === 'failed')
    return props.compaction.message || t('上下文压缩失败，聊天记录已保留。')
  if (props.compaction.status === 'skipped')
    return props.compaction.reason === 'no_compressible_history'
      ? t('没有可压缩的历史，未执行压缩。')
      : props.compaction.reason || t('此次压缩已跳过。')
  return ''
})
const counts = computed(() => {
  const { beforeTokens, afterTokens, measurement } = props.compaction
  if (props.compaction.status !== 'completed' || beforeTokens === null || afterTokens === null)
    return ''
  return t('上下文 {0} → {1} Token{2}', [
    beforeTokens.toLocaleString(),
    afterTokens.toLocaleString(),
    measurement === 'estimate' ? t('（估算）') : '',
  ])
})
</script>
<template>
  <section
    class="compaction-status"
    :class="{ compact, running, failed: compaction.status === 'failed' }"
    :role="announce ? (compaction.status === 'failed' ? 'alert' : 'status') : undefined"
    :aria-live="announce ? (compaction.status === 'failed' ? 'assertive' : 'polite') : undefined"
    :aria-atomic="announce ? true : undefined"
  >
    <div class="compaction-heading">
      <Pulse
        v-if="running"
        state="acting"
        aria-hidden="true"
      />
      <IconSymbol
        v-else
        :name="compaction.status === 'completed' ? 'check' : 'alert'"
        :size="14"
      />
      <strong>{{ label }}</strong>
      <span v-if="counts">{{ counts }}</span>
    </div>
    <p v-if="detail">
      {{ detail }}
    </p>
  </section>
</template>
<style scoped lang="scss">
.compaction-status {
  margin: var(--space-3) 0 var(--space-4) calc(var(--rail-width) + var(--space-4));
  padding: var(--space-3) var(--space-4);
  border-left: var(--hairline) solid var(--rule);
  color: var(--ink-2);
  font-size: var(--text-xs);
  line-height: var(--leading-small);
  overflow-wrap: anywhere;
}
.compaction-status.running {
  border-left-color: var(--accent);
}
.compaction-status.failed {
  border-left-color: var(--wait);
}
.compaction-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}
strong {
  color: var(--ink);
  font-weight: var(--weight-medium);
}
span {
  font-variant-numeric: tabular-nums;
}
p {
  margin: var(--space-2) 0 0;
}
.compaction-status.compact {
  max-width: 100%;
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-left: 0;
}
</style>
