<script setup lang="ts">
import CollapseCard from '../common/CollapseCard.vue'
import TerminalOutput from './TerminalOutput.vue'

defineProps<{
  outputs?: Array<{ tool_name: string; output: string; error?: string; duration_ms?: number }>
}>()
</script>

<template>
  <CollapseCard
    v-if="outputs?.length"
    :title="$t('{0} 次工具调用', [outputs.length])"
    icon="terminal"
  >
    <TerminalOutput
      v-for="output in outputs"
      :key="`${output.tool_name}:${output.duration_ms}:${output.output.length}`"
      :content="output.error || output.output"
      :title="output.tool_name"
    />
  </CollapseCard>
</template>
