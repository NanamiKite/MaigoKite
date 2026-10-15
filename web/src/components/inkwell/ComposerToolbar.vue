<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { PermissionMode } from '@/api/client'
import { composerDefaults, type ComposerProps } from '@/utils/composer-presentation'
import ModePill from './ModePill.vue'
import ModelPicker from './ModelPicker.vue'
import ThinkingControl from './ThinkingControl.vue'
import Pulse from '@/components/shell/Pulse.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkSelect from '@/components/common/InkSelect.vue'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
defineOptions({ inheritAttrs: false })
const props = withDefaults(
  defineProps<
    ComposerProps & {
      mode: 'ask' | 'plan' | 'auto'
      busy: boolean
      hasDraft: boolean
      guideDisabled: boolean
    }
  >(),
  composerDefaults,
)
const emit = defineEmits<{
  (event: 'reference' | 'cancel'): void
  (event: 'send', later?: boolean): void
  (event: 'mode', value: 'ask' | 'plan' | 'auto'): void
  (event: 'model-change', value: string): void
  (event: 'thinking-change', value: string): void
  (event: 'permission-mode-change', value: PermissionMode): void
}>()
const menuOpen = ref(false),
  trigger = ref<HTMLButtonElement | null>(null),
  menu = ref<HTMLElement | null>(null)
const session = useSessionStore()
const ui = useUIStore()
const sendShortcut = computed(() =>
  ui.appearance.enterBehavior === 'send' ? 'Enter' : 'Alt+S',
)
const confirmFullAccess = ref(false)
watch(() => [props.sessionId, props.permissionMode], () => { confirmFullAccess.value = false })
function choosePermission(event: Event) {
  const select = event.target as HTMLSelectElement
  const value = select.value as PermissionMode
  select.value = props.permissionMode
  if (value === 'danger_full_access') {
    confirmFullAccess.value = Boolean(session.capabilities.permission_boundary_v2)
  } else {
    confirmFullAccess.value = false
    emit('permission-mode-change', value)
  }
}
function enableFullAccess() {
  if (!session.capabilities.permission_boundary_v2 || props.isSubmitting) return
  confirmFullAccess.value = false
  emit('permission-mode-change', 'danger_full_access')
}
function closeMenu() {
  if (!menuOpen.value) return false
  menuOpen.value = false
  trigger.value?.focus()
  return true
}
async function toggleMenu() {
  menuOpen.value = !menuOpen.value
  if (menuOpen.value) {
    await nextTick()
    menu.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
  }
}
function send(later = props.isSending) {
  menuOpen.value = false
  emit('send', later)
}
function menuKey(event: KeyboardEvent) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  const buttons = [
    ...(menu.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []),
  ]
  if (!buttons.length) return
  event.preventDefault()
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  buttons[
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
  ].focus()
}
defineExpose({ closeMenu })
</script>
<template>
  <div class="composer-controls">
    <button
      class="reference-button"
      :aria-label="$t('引用文件、会话或记忆')"
      @click="emit('reference')"
    >
      <IconSymbol
        name="at"
        :size="16"
      />
    </button>
    <ModePill
      :model-value="mode"
      :disabled="isSending || isSubmitting"
      @update:model-value="emit('mode', $event)"
    />
    <div class="control-spacer" />
    <ModelPicker
      :key="sessionId"
      :model-value="modelId"
      :models="modelOptions"
      :disabled="isSending || isSubmitting || savingThinking || !modelOptions.length"
      @update:model-value="emit('model-change', $event)"
    />
    <ThinkingControl
      :model-id="modelId"
      :models="modelOptions"
      :model-value="thinking"
      :saving="savingThinking"
      :disabled="isSending || isSubmitting"
      @update:model-value="emit('thinking-change', $event)"
    />
    <InkSelect
      :value="permissionMode"
      :disabled="isSubmitting"
      :options="[
        { value: 'read_only', label: $t('只读') },
        { value: 'workspace', label: $t('工作区') },
        ...(['full_access', 'workspace_auto'].includes(permissionMode) ? [{ value: permissionMode, label: $t('受限自动'), disabled: true }] : []),
        ...(session.capabilities.permission_boundary_v2 || permissionMode === 'danger_full_access' ? [{ value: 'danger_full_access', label: $t('完全访问') }] : []),
      ]"
      :aria-label="$t('执行权限')"
      :title="$t('权限调整从下一个尚未开始的操作生效')"
      @change="choosePermission"
    />
    <div
      v-if="confirmFullAccess"
      class="permission-confirm"
      role="alert"
    >
      <span>{{ $t('完全访问会在本机执行命令、访问工作区外文件和网络，不再逐项审批。是否开启？') }}</span>
      <button
        class="danger-button"
        :disabled="isSubmitting"
        @click="enableFullAccess"
      >
        {{ $t('开启完全访问') }}
      </button>
      <button
        class="secondary-button"
        @click="confirmFullAccess = false"
      >
        {{ $t('取消') }}
      </button>
    </div>
    <button
      v-if="isSending"
      class="icon-button stop-button"
      :disabled="isStopping"
      :aria-busy="isStopping || undefined"
      :aria-label="isStopping ? $t('正在请求停止') : $t('停止任务')"
      @click="emit('cancel')"
    >
      <Pulse
        v-if="isStopping"
        state="acting"
      /><IconSymbol
        v-else
        name="stop"
        :size="15"
      />
    </button>
    <button
      class="send-button"
      :disabled="busy || !hasDraft || !modelOptions.length"
      :aria-busy="busy || undefined"
      :aria-label="busy ? $t('正在发送消息') : isSending ? $t('加入队列') : $t('发送消息')"
      :title="isSending ? $t('加入队列（{0}）', [sendShortcut]) : sendShortcut"
      :aria-keyshortcuts="sendShortcut"
      @click="send()"
    >
      <Pulse
        v-if="busy"
        state="acting"
      /><IconSymbol
        v-else
        :name="isSending ? 'clock' : 'send'"
        :size="15"
      />
    </button>
    <div
      v-if="isSending"
      class="send-options"
      @keydown.esc.stop.prevent="closeMenu"
      @keydown="menuKey"
    >
      <button
        ref="trigger"
        class="icon-button"
        :aria-label="$t('发送选项')"
        :aria-expanded="menuOpen"
        :disabled="busy"
        @click="toggleMenu"
      >
        <IconSymbol
          name="chevron"
          :size="15"
        />
      </button>
      <div
        v-if="menuOpen"
        ref="menu"
        class="send-options-menu"
      >
        <button
          :disabled="busy || !hasDraft"
          @click="send(true)"
        >
          {{ $t('加入队列') }}<small>{{ sendShortcut }}</small>
        </button>
        <button
          :disabled="busy || guideDisabled || !hasDraft"
          :title="guideReason || undefined"
          @click="send(false)"
        >
          {{ $t('立即发送') }}
        </button>
      </div>
    </div>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/composer-controls';
.permission-confirm {
  flex: 1 0 100%;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: var(--hairline) solid var(--seal);
  font-size: var(--text-xs);
}
.permission-confirm span { flex-basis: 100%; }
</style>
