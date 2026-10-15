<script setup lang="ts">
import CollapseCard from '../common/CollapseCard.vue'
import ChangeItem from './ChangeItem.vue'

defineProps<{
  changes?: Array<{
    file_path: string
    status: 'accepted' | 'rejected' | 'pending' | 'modified'
    additions: number
    deletions: number
    diff?: string
  }>
}>()
</script>

<template>
  <CollapseCard
    v-if="changes?.length"
    :title="$t('{0} 个文件更改', [changes.length])"
    icon="file"
  >
    <ChangeItem
      v-for="change in changes"
      :key="change.file_path"
      :change="change"
    />
  </CollapseCard>
</template>
