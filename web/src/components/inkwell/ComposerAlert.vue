<script setup lang="ts">
import { computed } from 'vue'
import { useRunStore } from '@/stores/run'
import CompactionStatus from './CompactionStatus.vue'

const props = defineProps<{ sessionId: string }>()
const run = useRunStore()
const turn = computed(() => (run.turns[props.sessionId] || []).slice(-1)[0])
</script>

<template>
  <div
    v-if="turn?.compaction?.status === 'failed'"
    class="composer-alert"
  >
    <CompactionStatus
      :compaction="turn.compaction"
      :turn-status="turn.status"
      compact
      announce
    />
  </div>
</template>

<style scoped lang="scss">
.composer-alert {
  margin-bottom: var(--space-4);
  border-inline-start: var(--pending-line) solid var(--wait);
}
</style>
