<script setup lang="ts">
import { t } from '@/i18n'
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useUIStore } from '@/stores/ui'
import { useMentions } from '@/composables/useMentions'
import { useComposerKeyboard } from '@/composables/useComposerKeyboard'
import type { Mention } from '@/components/inkwell/MentionMenu.vue'
import type { SlashCommand } from '@/components/inkwell/SlashMenu.vue'
import {
  composerDefaults,
  type ComposerProps,
  type ComposerEmits,
} from '@/utils/composer-presentation'
import ComposerToolbar from '@/components/inkwell/ComposerToolbar.vue'
import ComposerFooter from '@/components/inkwell/ComposerFooter.vue'
import ComposerAlert from '@/components/inkwell/ComposerAlert.vue'
import ComposerDraft from '@/components/inkwell/ComposerDraft.vue'
import ComposerHint from '@/components/inkwell/ComposerHint.vue'
import ImageAttachments from '@/components/inkwell/ImageAttachments.vue'
import type { ImageReference } from '@/api/images'
const props = withDefaults(defineProps<ComposerProps>(), composerDefaults)
const emit = defineEmits<ComposerEmits>()
const toolbar = ref<InstanceType<typeof ComposerToolbar> | null>(null),
  sent = ref(0)
const hint = ref<InstanceType<typeof ComposerHint> | null>(null)
const menuId = computed(() => 'inkwell-' + props.sessionId)
const inputValue = ref(''),
  textarea = ref<InstanceType<typeof ComposerDraft> | null>(null)
const submitting = ref(false),
  submitError = ref('')
const images = ref<ImageReference[]>([]),
  uploading = ref(false)
const busy = computed(() => props.isSubmitting || submitting.value || uploading.value)
const guideDisabled = computed(() => Boolean(props.waiting || props.guideReason))
const showGuideHelp = ref(false)
let draftRevision = 0
const mode = ref<'ask' | 'plan' | 'auto'>(props.permissionMode === 'read_only' ? 'ask' : 'auto')
const ui = useUIStore(),
  router = useRouter(),
  mentions = useMentions(inputValue)
const commands: SlashCommand[] = [
  { id: 'new', label: '新页' },
  { id: 'memory', label: '它记得' },
  { id: 'settings', label: '设置' },
  { id: 'files', label: '工作区文件' },
  { id: 'changes', label: '查看变更' },
  { id: 'plan', label: '查看计划' },
  { id: 'stop', label: '中断任务' },
]
const slashItems = computed(() =>
  inputValue.value.startsWith('/') && !inputValue.value.includes(' ')
    ? commands.filter((c) => c.id.includes(inputValue.value.slice(1)))
    : [],
)
watch(inputValue, () => {
  draftRevision++
})
watch(
  () => props.permissionMode,
  (value) => {
    if (mode.value !== 'plan' || value !== 'read_only')
      mode.value = value === 'read_only' ? 'ask' : 'auto'
  },
)
watch(
  () => props.sessionId,
  () => {
    inputValue.value = ''
    images.value = []
    uploading.value = false
  },
)
function chooseMode(value: 'ask' | 'plan' | 'auto') {
  mode.value = value
  emit('permission-mode-change', value === 'auto' ? 'workspace' : 'read_only')
}
async function send(later = props.isSending) {
  const content = inputValue.value.trim() || (images.value.length ? t('请描述这些图片。') : '')
  if (!content || !props.modelOptions.length || busy.value) return
  if (images.value.length && props.isSending) {
    submitError.value = t('请等待当前任务结束后再发送图片。')
    return
  }
  if (props.isSending && !later && guideDisabled.value) {
    submitError.value = props.guideReason || t('请先处理审批或回答问题；也可以在驳回时附上理由。')
    return
  }
  const intent = props.isSending ? (later ? 'later' : 'guide') : 'send'
  const payload =
    mode.value === 'plan' && intent !== 'guide'
      ? '请先分析并列出计划，暂不执行修改。\n\n' + content
      : content
  toolbar.value?.closeMenu()
  submitError.value = ''
  if (!props.submit) {
    emit('send', payload)
    sent.value++
    inputValue.value = ''
    return
  }
  const id = props.sessionId,
    draft = inputValue.value,
    revision = draftRevision
  submitting.value = true
  if (intent === 'guide') {
    try {
      if (localStorage.getItem('maigokite.steering-help') !== 'seen') {
        showGuideHelp.value = true
        localStorage.setItem('maigokite.steering-help', 'seen')
      }
    } catch {
      showGuideHelp.value = true
    }
  }
  try {
    await props.submit(
      payload,
      intent,
      images.value.map((image) => ({ ...image })),
    )
    sent.value++
    if (props.sessionId === id && inputValue.value === draft && draftRevision === revision) {
      inputValue.value = ''
      images.value = []
    }
  } catch (reason) {
    if (props.sessionId === id)
      submitError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    submitting.value = false
  }
}
function chooseMention(item: Mention) {
  mentions.select(item)
  void nextTick(() => textarea.value?.focus())
}
function reference() {
  inputValue.value += ' @'
  textarea.value?.focus()
}
defineExpose({
  focus() {
    textarea.value?.focus()
  },
  restoreDraft(content: string) {
    const draft =
      mode.value === 'plan'
        ? content.replace(/^请先分析并列出计划，暂不执行修改。\n\n/, '')
        : content
    inputValue.value = inputValue.value ? `${inputValue.value}\n\n${draft}` : draft
    void nextTick(() => textarea.value?.focus())
  },
})
function command(item: SlashCommand) {
  inputValue.value = ''
  if (item.id === 'new') void router.push('/chat')
  else if (['memory', 'settings'].includes(item.id)) void router.push('/' + item.id)
  else if (item.id === 'stop') emit('cancel')
  else ui.openDrawer(item.id as 'files' | 'changes' | 'plan')
}
const handleKeydown = useComposerKeyboard({
  draft: inputValue,
  context: () => [props.sessionId, props.modelId, mode.value, props.isSending],
  lastMessage: () => props.lastMessage,
  canSend: () =>
    Boolean(
      (inputValue.value.trim() || images.value.length) && props.modelOptions.length && !busy.value,
    ),
  isSending: () => props.isSending,
  closeMenu: () => toolbar.value?.closeMenu() || false,
  send,
  cancel: () => emit('cancel'),
})
</script>
<template>
  <div
    class="composer-wrap"
    :class="{ 'is-running': isSending, 'is-waiting': waiting, 'has-queue': queuedCount > 0 }"
  >
    <ComposerAlert :session-id="sessionId" />
    <div
      class="composer"
      :aria-busy="isSending || busy"
    >
      <svg
        v-if="sent"
        :key="sent"
        class="send-drop"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path d="M12 2C10 7 5 11 5 15a7 7 0 0 0 14 0c0-4-5-8-7-13Z" />
      </svg>
      <ComposerDraft
        ref="textarea"
        v-model="inputValue"
        :menu-id="menuId"
        :mentions="mentions.items.value"
        :mention-query="mentions.query.value"
        :loading="mentions.loading.value"
        :commands="slashItems"
        :placeholder="
          isSending
            ? $t('写下一条消息…')
            : waiting
              ? $t('等你确认后继续…')
              : $t('问问 ') + assistantName + '…'
        "
        :disabled="!modelOptions.length"
        @mention="chooseMention"
        @command="command"
        @focus="hint?.show()"
        @keydown="handleKeydown"
      />
      <ComposerHint ref="hint" />
      <ImageAttachments
        :key="sessionId"
        v-model="images"
        :disabled="busy || isSending || !props.submit"
        @busy="uploading = $event"
      />
      <ComposerToolbar
        ref="toolbar"
        v-bind="props"
        :mode="mode"
        :busy="busy"
        :has-draft="Boolean(inputValue.trim() || images.length)"
        :guide-disabled="guideDisabled"
        @reference="reference"
        @mode="chooseMode"
        @cancel="emit('cancel')"
        @send="send"
        @model-change="emit('model-change', $event)"
        @thinking-change="emit('thinking-change', $event)"
        @permission-mode-change="emit('permission-mode-change', $event)"
      />
    </div>
    <p
      v-if="submitError"
      class="composer-notice"
      role="alert"
    >
      {{ submitError }}
    </p>
    <p
      v-if="showGuideHelp"
      class="composer-notice"
      role="status"
    >
      {{ $t('补充会让下一步操作暂停并重新考虑，已执行的操作不会撤回。') }}
      <button
        class="text-button"
        @click="showGuideHelp = false"
      >
        {{ $t('知道了') }}
      </button>
    </p>
    <ComposerFooter
      v-bind="props"
      :busy="busy"
    />
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/input-box';
</style>
