<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { ImageReference } from '@/api/images'
import { useUIStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'

const props = defineProps<{ image: ImageReference }>()
const container = ref<HTMLElement | null>(null),
  url = ref(''),
  error = ref(''),
  busy = ref(false)
const ui = useUIStore(),
  workspace = useWorkspaceStore()
let controller: AbortController | undefined, observer: IntersectionObserver | undefined
let visible = false
async function load() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  busy.value = true
  error.value = ''
  url.value = ''
  try {
    const file = await api.getWorkspaceFile(props.image.path, props.image.folder_id, request.signal)
    if (request.signal.aborted) return
    if (!file.data_url || file.truncated) throw new Error(t('图片暂时无法预览，请打开文件查看。'))
    url.value = file.data_url
  } catch (reason) {
    if (!request.signal.aborted)
      error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (!request.signal.aborted) busy.value = false
  }
}
function open() {
  workspace.select(props.image.folder_id)
  ui.openFilePreview(props.image.path, props.image.folder_id)
}
watch(
  () => [props.image.path, props.image.folder_id, props.image.sha256],
  () => {
    if (visible) void load()
  },
)
onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      visible = true
      observer?.disconnect()
      void load()
    },
    { rootMargin: '200px' },
  )
  if (container.value) observer.observe(container.value)
})
onBeforeUnmount(() => {
  controller?.abort()
  observer?.disconnect()
  url.value = ''
})
</script>
<template>
  <figure
    ref="container"
    class="workspace-image"
  >
    <button
      type="button"
      class="image-link"
      :aria-label="$t('打开图片文件')"
      @click="open"
    >
      <img
        v-if="url && !error"
        :src="url"
        :alt="image.path"
        loading="lazy"
        @error="error = $t('图片无法解码，请检查文件是否完整。')"
      />
      <span v-else>{{ busy ? $t('正在读取图片…') : $t('打开图片文件') }}</span>
    </button>
    <figcaption>
      <button
        type="button"
        @click="open"
      >
        {{ image.path }}
      </button>
    </figcaption>
    <p
      v-if="error"
      role="status"
    >
      {{ error }}
      <button
        type="button"
        :disabled="busy"
        @click="load"
      >
        {{ $t('重试') }}
      </button>
    </p>
  </figure>
</template>
<style scoped lang="scss">
.workspace-image {
  margin: var(--space-3) 0;
  max-width: min(100%, 520px);
}
.image-link {
  display: block;
  min-width: 120px;
  min-height: 80px;
  max-width: 100%;
  border-radius: var(--radius-md);
  overflow: hidden;
}
img {
  display: block;
  max-width: 100%;
  max-height: 360px;
  object-fit: contain;
}
figcaption {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  overflow-wrap: anywhere;
  color: var(--ink-3);
}
p {
  font-size: var(--text-xs);
}
</style>
