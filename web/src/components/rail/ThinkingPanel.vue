<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { t } from '@/i18n'
import type { PulseState, Turn } from '@/api/events'
import IconSymbol from '@/components/common/IconSymbol.vue'
import MarkdownContent from '@/components/chat-area/MarkdownContent.vue'
import Pulse from '@/components/shell/Pulse.vue'

const props = defineProps<{
  text: string
  status: Turn['status']
  thinkingStatus?: Turn['thinkingStatus']
}>()
const running = computed(() => props.status === 'running' && props.thinkingStatus === 'running')
const expanded = ref(running.value)
let manuallyToggled = false
const label = computed(() => {
  if (props.status === 'cancelled') return t('已停止')
  if (props.status === 'failed') return t('执行遇到问题')
  return t(running.value ? '正在思考' : '思考已结束')
})
const pulse = computed<PulseState>(() =>
  props.status === 'failed' ? 'error' : running.value ? 'thinking' : 'idle',
)
// Incoming tokens never override the user's disclosure choice. Untouched
// panels follow the actual reasoning phase, not the whole tool-running task.
watch(running, (value) => {
  if (!manuallyToggled) expanded.value = value
})
function toggle() {
  manuallyToggled = true
  expanded.value = !expanded.value
}
function syncExpanded(event: Event) {
  if (event.currentTarget instanceof HTMLDetailsElement) expanded.value = event.currentTarget.open
}
</script>

<template>
  <details
    class="thinking-panel"
    :class="{ running }"
    :open="expanded"
    @toggle="syncExpanded"
  >
    <summary
      class="thinking-heading"
      @click.prevent="toggle"
    >
      <Pulse :state="pulse" />
      <strong>{{ $t('思考') }}</strong>
      <span
        class="thinking-status"
        role="status"
        aria-live="polite"
      >
        {{ label }}
      </span>
      <span class="thinking-action">{{ $t(expanded ? '收起' : '展开') }}</span>
      <IconSymbol
        name="chevron"
        :size="12"
        class="thinking-disclosure"
      />
    </summary>
    <div
      v-if="expanded"
      class="thinking-content"
      role="region"
      :aria-label="$t('思考内容')"
      tabindex="0"
    >
      <MarkdownContent
        v-if="text"
        :content="text"
        :is-streaming="running"
      />
      <p v-else>
        {{ $t('正在思考…') }}
      </p>
    </div>
  </details>
</template>

<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.thinking-panel {
  margin: var(--space-3) 0 var(--space-4) calc(var(--rail-width) + var(--space-4));
  border-left: var(--hairline) solid var(--rule);
  color: var(--ink-2);
}
.thinking-panel.running {
  border-left-color: var(--accent);
}
.thinking-heading {
  @include mixins.interactive-row;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--rail-node-height);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  list-style: none;
  cursor: pointer;
}
.thinking-heading::-webkit-details-marker {
  display: none;
}
.thinking-heading strong {
  color: var(--ink);
  font-weight: var(--weight-medium);
}
.thinking-status {
  @include mixins.ellipsis;
  flex: 1;
  min-width: 0;
}
.thinking-action {
  flex: none;
  color: var(--ink-2);
}
.thinking-disclosure {
  color: var(--ink-3);
  transition: transform var(--motion-fast) var(--ease);
}
.thinking-panel[open] .thinking-disclosure {
  transform: rotate(var(--measure-90deg));
}
.thinking-content {
  max-height: var(--thinking-max-height);
  padding: var(--space-3) var(--space-4);
  overflow: auto;
  overflow-wrap: anywhere;
  font-size: var(--text-sm);
  line-height: var(--leading-small);
}
.thinking-content :deep(.markdown-content) {
  color: var(--ink-2);
  font-size: inherit;
  line-height: inherit;
}
.thinking-content p {
  margin: 0;
}
</style>
