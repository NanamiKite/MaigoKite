<script setup lang="ts">
import type { PulseState } from '@/api/events'
withDefaults(defineProps<{ state?: PulseState; size?: number }>(), { state: 'idle', size: 10 })
const labels = {
  idle: '静息',
  thinking: '思考',
  acting: '行动',
  waiting: '等待确认',
  error: '出错',
  offline: '离线',
}
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 16 16"
    class="pulse"
    :class="state"
    role="img"
    :aria-label="$t(labels[state])"
  >
    <path
      v-if="state === 'error'"
      d="M4 4l8 8M12 4l-8 8"
    />
    <path
      v-else-if="state === 'waiting'"
      d="M8 2l6 6-6 6-6-6Z"
    />
    <rect
      v-else-if="state === 'acting'"
      x="2"
      y="6"
      width="12"
      height="4"
      rx="2"
    />
    <circle
      v-else
      cx="8"
      cy="8"
      r="5"
    />
  </svg>
</template>
<style scoped lang="scss">
.pulse {
  flex: none;
  overflow: visible;
  color: var(--ink-3);
  fill: currentColor;
  stroke: currentColor;
  stroke-width: 1.5;
}
.thinking,
.acting {
  color: var(--accent);
  transform-origin: center;
}
.thinking {
  animation: ink-breathe var(--motion-pulse) var(--ease) infinite;
}
.acting {
  animation: ink-act var(--motion-pulse) var(--ease) infinite;
}
.waiting {
  color: var(--wait);
  animation: ink-wait var(--duration-slow) var(--ease);
}
.error {
  color: var(--ink-2);
  fill: var(--transparent);
}
.offline {
  fill: var(--transparent);
}
</style>
