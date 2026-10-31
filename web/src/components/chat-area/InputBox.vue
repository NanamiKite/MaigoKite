<script setup lang="ts">
import { vDeskPaper } from '@/utils/desk-paper'
import { t } from '@/i18n'
import { computed, nextTick, ref, watch } from 'vue'
import { useMentions } from '@/composables/useMentions'
import { useComposerCommands } from '@/composables/useComposerCommands'
import { useComposerKeyboard } from '@/composables/useComposerKeyboard'
import { useChatDraft } from '@/composables/useChatDraft'
import { useWorkspaceStore } from '@/stores/workspace'
import type { Mention } from '@/components/inkwell/MentionMenu.vue'
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
import { getInputCapabilities, type AttachmentReference } from '@/api/images'
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
const images = ref<AttachmentReference[]>([]),
  pendingFiles = ref<File[]>([]),
  uploading = ref(false)
const attachments = ref<InstanceType<typeof ImageAttachments> | null>(null)
const commandsOpen = ref(false)
const imageSupported = ref<boolean | null>(null),
  imageFallback = ref(false),
  checkingInputs = ref(false),
  capabilityError = ref('')
const hasImages = computed(() => images.value.some((item) => item.type === 'image'))
const showImageNotice = computed(
  () =>
    hasImages.value &&
    (imageSupported.value === false ||
      imageFallback.value ||
      checkingInputs.value ||
      capabilityError.value),
)
const attachmentsReady = computed(
  () =>
    !pendingFiles.value.length &&
    !checkingInputs.value &&
    !(hasImages.value && imageSupported.value === false),
)
watch(
  [() => props.modelId, hasImages],
  async ([modelId, present], _, onCleanup) => {
    imageSupported.value = null
    imageFallback.value = false
    capabilityError.value = ''
    checkingInputs.value = present
    if (!present) return
    const request = new AbortController()
    onCleanup(() => request.abort())
    try {
      const result = await getInputCapabilities(modelId, request.signal)
      if (!request.signal.aborted) {
        imageSupported.value = result.image_fallback === 'ocr' ? true : result.image_input
        imageFallback.value = result.image_fallback === 'ocr'
      }
    } catch {
      if (!request.signal.aborted) capabilityError.value = t('未能确认看图能力；发送时会再次检查。')
    } finally {
      if (!request.signal.aborted) checkingInputs.value = false
    }
  },
  { flush: 'sync' },
)
const busy = computed(() => props.isSubmitting || submitting.value || uploading.value)
const guideDisabled = computed(() => Boolean(props.waiting || props.guideReason))
const showGuideHelp = ref(false)
let draftRevision = 0
const mode = ref<'ask' | 'plan' | 'auto'>(props.permissionMode === 'read_only' ? 'ask' : 'auto')
const workspace = useWorkspaceStore()
const draftKey = computed(() => props.sessionId || 'draft:' + workspace.selectedId)
const draftCache = useChatDraft(draftKey, inputValue, images, pendingFiles, mode, () =>
  props.permissionMode === 'read_only' ? 'ask' : 'auto',
)
const mentions = useMentions(inputValue)
const commandMenu = useComposerCommands({
  open: () => commandsOpen.value,
  imageDisabled: () => busy.value || props.isSending || !props.submit,
  isSending: () => props.isSending,
  image: () => attachments.value?.choose(),
  reference,
  stop: () => emit('cancel'),
  insert(text) {
    inputValue.value = `${text}\n\n${inputValue.value}`
    void nextTick(() => textarea.value?.focus())
  },
})
watch(
  [inputValue, images, pendingFiles, mode],
  () => {
    draftRevision++
  },
  { deep: true, flush: 'sync' },
)
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
    submitError.value = ''
    toolbar.value?.closeMenu()
    uploading.value = false
  },
)
function chooseMode(value: 'ask' | 'plan' | 'auto') {
  mode.value = value
  emit('permission-mode-change', value === 'auto' ? 'workspace' : 'read_only')
}
async function send(later = props.isSending) {
  const content = inputValue.value.trim() || (images.value.length ? t('请查看这些附件。') : '')
  if (!content || !props.modelOptions.length || busy.value || !attachmentsReady.value) return
  if (images.value.length && props.isSending) {
    submitError.value = t('请等待当前任务结束后再发送附件。')
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
  textarea.value?.closeMenu()
  submitError.value = ''
  if (!props.submit) {
    emit('send', payload)
    sent.value++
    inputValue.value = ''
    return
  }
  const id = props.sessionId,
    key = draftKey.value,
    draft = inputValue.value,
    revision = draftRevision
  const sentDraft = {
    text: draft,
    images: images.value.map((image) => ({ ...image })),
    pendingFiles: [],
    mode: mode.value,
  }
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
    await props.submit(payload, intent, sentDraft.images)
    sent.value++
    draftCache.clearSent(key, sentDraft)
    if (props.sessionId === id && inputValue.value === draft && draftRevision === revision) {
      inputValue.value = ''
      images.value = []
      draftCache.save(key)
    }
  } catch (reason) {
    if (props.sessionId === id)
      submitError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    submitting.value = false
  }
}
async function chooseMention(item: Mention) {
  const key = draftKey.value,
    draft = inputValue.value
  if (item.kind === '文件' && !(await attachments.value?.reference(item.label))) return
  if (draftKey.value !== key || inputValue.value !== draft) return
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
const handleKeydown = useComposerKeyboard({
  draft: inputValue,
  context: () => [props.sessionId, props.modelId, mode.value, props.isSending],
  lastMessage: () => props.lastMessage,
  canSend: () =>
    Boolean(
      (inputValue.value.trim() || images.value.length) &&
      props.modelOptions.length &&
      !busy.value &&
      attachmentsReady.value,
    ),
  isSending: () => props.isSending,
  closeMenu: () => textarea.value?.closeMenu() || toolbar.value?.closeMenu() || false,
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
      v-desk-paper
      class="composer"
      :aria-busy="isSending || busy"
      @paste="attachments?.paste($event)"
      @dragenter="attachments?.dragEnter($event)"
      @dragleave="attachments?.dragLeave($event)"
      @dragover="attachments?.dragOver($event)"
      @drop="attachments?.drop($event)"
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
      <ImageAttachments
        :key="draftKey + ':' + workspace.selectedId"
        ref="attachments"
        v-model="images"
        v-model:pending-files="pendingFiles"
        :disabled="isSubmitting || submitting || isSending || !props.submit"
        @busy="uploading = $event"
      />
      <ComposerDraft
        ref="textarea"
        v-model="inputValue"
        :menu-id="menuId"
        :mentions="mentions.items.value"
        :mention-query="mentions.query.value"
        :loading="mentions.loading.value"
        :commands="commandMenu.commands.value"
        :commands-loading="commandMenu.loading.value"
        :commands-error="commandMenu.error.value"
        :placeholder="
          isSending
            ? $t('写下一条消息…')
            : waiting
              ? $t('等你确认后继续…')
              : $t('问问 ') + assistantName + '…'
        "
        :disabled="!modelOptions.length"
        @mention="chooseMention"
        @command="commandMenu.select"
        @retry-commands="commandMenu.retry"
        @commands-open="commandsOpen = $event"
        @focus="hint?.show()"
        @keydown="handleKeydown"
      />
      <ComposerHint ref="hint" />
      <ComposerToolbar
        ref="toolbar"
        v-bind="props"
        :mode="mode"
        :busy="busy"
        :has-draft="Boolean(inputValue.trim() || images.length) && attachmentsReady"
        :guide-disabled="guideDisabled"
        :commands-open="commandsOpen"
        :commands-id="menuId + '-commands'"
        @commands="textarea?.toggleCommands()"
        @mode="chooseMode"
        @cancel="emit('cancel')"
        @send="send"
        @model-change="emit('model-change', $event)"
        @thinking-change="emit('thinking-change', $event)"
        @permission-mode-change="emit('permission-mode-change', $event)"
      />
    </div>
    <p
      v-if="showImageNotice"
      class="composer-notice"
      role="status"
    >
      {{
        checkingInputs
          ? $t('正在确认看图能力…')
          : imageSupported === false
            ? $t('当前模型不支持图片，请切换模型后发送。')
            : imageFallback
              ? $t('将识别图片中的文字，无法理解图形细节。')
              : capabilityError
      }}
    </p>
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
