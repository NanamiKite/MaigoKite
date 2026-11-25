<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { t } from '@/i18n'
import IconSymbol from '@/components/common/IconSymbol.vue'
import { vTooltip } from '@/utils/tooltip'

const props = defineProps<{ content: string }>()
const copied = ref(false),
  busy = ref(false),
  error = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false

function resetFeedback() {
  copied.value = false
  error.value = ''
  if (timer) clearTimeout(timer)
  timer = undefined
}
async function copy() {
  if (busy.value || !props.content) return
  const content = props.content
  resetFeedback()
  busy.value = true
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    // Copy the original message, including Markdown, not rendered HTML or tool activity.
    await navigator.clipboard.writeText(content)
    if (disposed || props.content !== content) return
    copied.value = true
    timer = setTimeout(() => {
      copied.value = false
      timer = undefined
    }, 1800)
  } catch {
    if (!disposed && props.content === content) error.value = t('无法复制，请选中文字后复制。')
  } finally {
    if (!disposed) busy.value = false
  }
}
watch(() => props.content, resetFeedback)
onScopeDispose(() => {
  disposed = true
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="message-action-row">
    <div class="message-action-row__buttons">
      <button
        v-tooltip="$t(copied ? '已复制' : '复制消息')"
        type="button"
        class="icon-button message-copy"
        :class="{ copied }"
        :disabled="busy || !content"
        :aria-label="$t(copied ? '已复制' : '复制消息')"
        :aria-busy="busy || undefined"
        @click="copy"
      >
        <IconSymbol
          :name="copied ? 'check' : 'copy'"
          :size="18"
        />
      </button>
      <slot />
    </div>
    <span
      class="sr-only"
      role="status"
      aria-live="polite"
    >
      {{ copied ? $t('已复制') : '' }}
    </span>
    <p
      v-if="error"
      class="message-action-row__error"
      role="alert"
    >
      {{ error }}
    </p>
  </div>
</template>

<style scoped lang="scss">
.message-action-row {
  margin-top: var(--space-3);
  max-width: 100%;
  color: var(--ink-2);
  font-size: var(--text-xs);
}
.message-action-row__buttons {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}
.message-copy {
  flex: none;
  width: var(--space-8);
  min-height: var(--space-8);
  color: var(--ink-2);
}
.message-copy.copied {
  color: var(--accent);
}
.message-action-row__error {
  margin: var(--space-2) 0 0;
  color: var(--seal);
  overflow-wrap: anywhere;
}
@media (pointer: coarse) {
  .message-copy {
    width: var(--measure-46px);
    min-height: var(--measure-46px);
  }
}
</style>
