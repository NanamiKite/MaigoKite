<script setup lang="ts">
import { computed } from 'vue'
import type { Step } from '@/api/events'
import { parseImages } from '@/api/images'
import WorkspaceImage from './WorkspaceImage.vue'

const props = defineProps<{ steps: Step[] }>()
const images = computed(() =>
  props.steps
    .filter((step) => step.tool === 'image_generate' && step.status === 'completed')
    .flatMap((step) => parseImages(step.output)),
)
</script>
<template>
  <div
    v-if="images.length"
    class="generated-images"
    :aria-label="$t('生成的图片')"
  >
    <WorkspaceImage
      v-for="image in images"
      :key="image.folder_id + ':' + image.path"
      :image="image"
    />
  </div>
</template>
