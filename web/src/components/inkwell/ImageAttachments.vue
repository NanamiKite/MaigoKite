<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { uploadImage, type ImageReference } from '@/api/images'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'
import WorkspaceImage from '@/components/chat-area/WorkspaceImage.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'

const props = defineProps<{ modelValue: ImageReference[]; disabled?: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [images: ImageReference[]]
  busy: [value: boolean]
}>()
const workspace = useWorkspaceStore()
const input = ref<HTMLInputElement | null>(null),
  busy = ref(false),
  error = ref('')
const pending = ref<File[]>([]),
  dragDepth = ref(0)
const hasPending = computed(() => pending.value.length > 0)
let controller: AbortController | undefined, reader: FileReader | undefined
function cancel() {
  controller?.abort()
  if (reader?.readyState === FileReader.LOADING) reader.abort()
}
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) cancel()
  },
)
function read(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const current = new FileReader()
    reader = current
    current.onload = () =>
      typeof current.result === 'string'
        ? resolve(current.result.slice(current.result.indexOf(',') + 1))
        : reject(new Error(t('图片读取失败')))
    current.onerror = () => reject(new Error(t('图片读取失败')))
    current.onabort = () => reject(new Error(t('已取消')))
    current.readAsDataURL(file)
  })
}
async function upload() {
  if (props.disabled || busy.value || !pending.value.length) return
  const request = new AbortController(),
    folderId = workspace.selectedId
  controller = request
  busy.value = true
  error.value = ''
  emit('busy', true)
  try {
    // Keep pick order and retain failed files for retry under one upload owner.
    for (const file of [...pending.value]) {
      const data = await read(file)
      if (request.signal.aborted) return
      const image = await uploadImage(data, folderId, request.signal)
      if (request.signal.aborted) return
      emit('update:modelValue', [...props.modelValue, image])
      pending.value.shift()
    }
  } catch (reason) {
    if (!request.signal.aborted)
      error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (controller === request) {
      controller = undefined
      busy.value = false
      emit('busy', false)
    }
  }
}
function add(files: File[]) {
  if (!files.length) return
  if (props.disabled || busy.value) {
    error.value = t('暂时无法添加图片')
    return
  }
  const batch = [...pending.value, ...files]
  if (
    batch.some(
      (file) => !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type),
    )
  ) {
    error.value = t('请选择 PNG、JPEG、WebP 或 GIF 图片。')
    return
  }
  const bytes = [...props.modelValue, ...batch].reduce((sum, file) => sum + file.size, 0)
  if (props.modelValue.length + batch.length > 4 || bytes > 16 * 1024 * 1024) {
    error.value = t('每条消息最多附带 4 张图片，总计不能超过 16 MiB。')
    return
  }
  pending.value = batch
  void upload()
}
function picked(event: Event) {
  const target = event.target as HTMLInputElement
  add(Array.from(target.files || []))
  target.value = ''
}
function paste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files || [])
  if (!files.length) return
  event.preventDefault()
  event.stopPropagation()
  add(files)
}
function isFileDrag(event: DragEvent) {
  return event.dataTransfer?.types.includes('Files') === true
}
function dragEnter(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  dragDepth.value++
}
function dragLeave(event: DragEvent) {
  if (isFileDrag(event)) dragDepth.value = Math.max(0, dragDepth.value - 1)
}
function dragOver(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer)
    event.dataTransfer.dropEffect = props.disabled || busy.value ? 'none' : 'copy'
}
function drop(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  event.stopPropagation()
  dragDepth.value = 0
  add(Array.from(event.dataTransfer?.files || []))
}
function removePending(index: number) {
  pending.value.splice(index, 1)
  if (!pending.value.length) error.value = ''
}
onBeforeUnmount(() => {
  cancel()
  controller = undefined
  emit('busy', false)
})
defineExpose({
  hasPending,
  paste,
  dragEnter,
  dragLeave,
  dragOver,
  drop,
  choose() {
    if (!props.disabled && !busy.value) input.value?.click()
  },
})
</script>
<template>
  <input
    ref="input"
    type="file"
    multiple
    hidden
    accept="image/png,image/jpeg,image/webp,image/gif"
    :disabled="disabled || busy"
    @change="picked"
  />
  <div
    v-if="dragDepth"
    class="image-drop-target"
    :class="{ unavailable: disabled || busy }"
    role="status"
  >
    <IconSymbol
      name="image"
      :size="24"
    />
    {{ disabled || busy ? $t('暂时无法添加图片') : $t('松开以添加图片') }}
  </div>
  <div
    v-if="modelValue.length || pending.length"
    class="image-attachments"
    role="group"
    :aria-label="$t('图片附件')"
  >
    <div
      v-for="(image, index) in modelValue"
      :key="image.path"
      class="image-chip"
    >
      <WorkspaceImage
        :image="image"
        compact
      />
      <button
        class="image-remove"
        type="button"
        :disabled="disabled || busy"
        :aria-label="$t('移除图片 {0}', [index + 1])"
        @click="
          emit(
            'update:modelValue',
            modelValue.filter((item) => item !== image),
          )
        "
      >
        <IconSymbol
          name="close"
          :size="12"
        />
      </button>
    </div>
    <div
      v-for="(file, index) in pending"
      :key="index"
      class="image-chip image-pending"
      :title="file.name"
    >
      <InkLoader v-if="busy && index === 0" />
      <IconSymbol
        v-else
        :name="error ? 'alert' : 'image'"
        :size="18"
      />
      <span>{{ file.name }}</span>
      <button
        class="image-remove"
        type="button"
        :disabled="disabled || busy"
        :aria-label="$t('移除图片 {0}', [modelValue.length + index + 1])"
        @click="removePending(index)"
      >
        <IconSymbol
          name="close"
          :size="12"
        />
      </button>
    </div>
  </div>
  <p
    v-if="busy"
    class="attachment-status"
    role="status"
  >
    {{ $t('正在添加图片…') }}
  </p>
  <p
    v-else-if="error || hasPending"
    class="attachment-status"
    role="alert"
  >
    {{ error || $t('图片尚未上传') }}
    <button
      v-if="hasPending"
      type="button"
      :disabled="disabled"
      @click="upload"
    >
      {{ $t('重试') }}
    </button>
    <button
      v-else
      type="button"
      @click="error = ''"
    >
      {{ $t('关闭') }}
    </button>
  </p>
</template>
<style scoped lang="scss">
@use '@/styles/image-attachments';
</style>
