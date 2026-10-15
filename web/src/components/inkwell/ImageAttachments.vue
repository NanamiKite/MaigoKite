<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { uploadImage, type ImageReference } from '@/api/images'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'

const props = defineProps<{ modelValue: ImageReference[]; disabled?: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [images: ImageReference[]]
  busy: [value: boolean]
}>()
const workspace = useWorkspaceStore(),
  busy = ref(false),
  error = ref('')
let controller: AbortController | undefined, reader: FileReader | undefined
function cancel() {
  controller?.abort()
  if (reader?.readyState === FileReader.LOADING) reader.abort()
}
watch(
  () => workspace.selectedId,
  () => {
    cancel()
    emit('update:modelValue', [])
    error.value = ''
  },
)
async function choose(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || props.disabled || busy.value) return
  error.value = ''
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
    error.value = t('请选择 PNG、JPEG、WebP 或 GIF 图片。')
    return
  }
  if (
    props.modelValue.length >= 4 ||
    file.size + props.modelValue.reduce((sum, image) => sum + image.size, 0) > 16 * 1024 * 1024
  ) {
    error.value = t('每条消息最多附带 4 张图片，总计不能超过 16 MiB。')
    return
  }
  const folder = workspace.selectedId
  const request = new AbortController()
  controller = request
  busy.value = true
  emit('busy', true)
  try {
    const data = await new Promise<string>((resolve, reject) => {
      reader = new FileReader()
      reader.onload = () =>
        typeof reader?.result === 'string'
          ? resolve(reader.result.slice(reader.result.indexOf(',') + 1))
          : reject(new Error(t('图片读取失败')))
      reader.onerror = () => reject(new Error(t('图片读取失败')))
      reader.onabort = () => reject(new Error(t('已取消')))
      reader.readAsDataURL(file)
    })
    if (request.signal.aborted) return
    const image = await uploadImage(data, folder, request.signal)
    if (!request.signal.aborted && workspace.selectedId === folder)
      emit('update:modelValue', [...props.modelValue, image])
  } catch (reason) {
    if (!request.signal.aborted)
      error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
    emit('busy', false)
  }
}
onBeforeUnmount(() => {
  cancel()
  emit('busy', false)
})
</script>
<template>
  <div class="image-attachments">
    <label
      class="attach-image"
      :class="{ disabled: disabled || busy }"
    >
      <input
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        :disabled="disabled || busy"
        @change="choose"
      />
      {{ busy ? $t('正在添加图片…') : $t('添加图片') }}
    </label>
    <span
      v-for="(image, index) in modelValue"
      :key="image.path"
      class="image-chip"
    >
      {{ $t('图片 {0}', [index + 1]) }}
      <button
        type="button"
        :disabled="disabled || busy"
        :aria-label="$t('移除图片')"
        @click="
          emit(
            'update:modelValue',
            modelValue.filter((item) => item !== image),
          )
        "
      >
        ×
      </button>
    </span>
    <small
      v-if="error"
      role="alert"
    >
      {{ error }}
    </small>
  </div>
</template>
<style scoped lang="scss">
.image-attachments {
  padding: var(--space-2) var(--space-4);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.attach-image {
  position: relative;
  overflow: hidden;
  cursor: pointer;
  font-size: var(--text-xs);
}
input {
  position: absolute;
  inset: 0;
  opacity: 0;
  width: 100%;
  cursor: pointer;
}
.disabled {
  opacity: 0.5;
  cursor: default;
}
.image-chip {
  border-radius: var(--radius-sm);
  background: var(--paper-3);
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-xs);
}
small {
  flex-basis: 100%;
  color: var(--ink-3);
}
</style>
