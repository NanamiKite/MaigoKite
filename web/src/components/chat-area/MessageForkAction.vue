<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import IconSymbol from '@/components/common/IconSymbol.vue'
import { vTooltip } from '@/utils/tooltip'

// The caller keys this component by the persisted message ID, not a task or stream ID.
const props = defineProps<{ conversationId: string; messageId?: string; disabled: boolean }>()
const session = useSessionStore(),
  router = useRouter()
const menu = ref<HTMLDetailsElement | null>(null)
const busy = ref(false),
  error = ref('')
const available = computed(
  () =>
    session.capabilities.conversation_fork &&
    props.messageId &&
    !props.messageId.startsWith('stream-'),
)
let requestKey = ''
let disposed = false

function close(restoreFocus = false) {
  if (!menu.value?.open) return
  menu.value.open = false
  if (restoreFocus) menu.value.querySelector('summary')?.focus()
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !menu.value?.contains(event.target)) close()
}
function toggle() {
  // Listen only while this reply's disclosure is open.
  document.removeEventListener('pointerdown', outside)
  if (menu.value?.open) document.addEventListener('pointerdown', outside)
}
function focusOut(event: FocusEvent) {
  if (event.relatedTarget instanceof Node && !menu.value?.contains(event.relatedTarget)) close()
}
async function fork() {
  if (!available.value || !props.messageId || props.disabled || busy.value) return
  const source = props.conversationId,
    message = props.messageId
  busy.value = true
  error.value = ''
  try {
    // An ambiguous response must reuse the same key and exact branch point.
    // Closing and reopening the menu does not reset it. Never retry automatically.
    requestKey ||= crypto.randomUUID()
    const branch = await session.forkSession(source, requestKey, message)
    if (!disposed && String(router.currentRoute.value.params.id || '') === source)
      await router.push('/chat/' + branch.id)
  } catch (reason) {
    if (!disposed) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      if (menu.value) menu.value.open = true
    }
  } finally {
    busy.value = false
  }
}
onScopeDispose(() => {
  disposed = true
  document.removeEventListener('pointerdown', outside)
})
</script>

<template>
  <details
    v-if="available"
    ref="menu"
    class="message-actions"
    @toggle="toggle"
    @focusout="focusOut"
    @keydown.esc.stop.prevent="close(true)"
  >
    <summary
      v-tooltip="$t('从此处分叉新对话')"
      class="icon-button"
      :aria-label="$t('从此处分叉新对话')"
    >
      <IconSymbol
        name="branch"
        :size="18"
      />
    </summary>
    <div class="message-actions__options">
      <button
        type="button"
        :disabled="disabled || busy"
        :aria-busy="busy || undefined"
        @click="fork"
      >
        {{ busy ? $t('正在创建分岔…') : $t('从此处分叉新对话') }}
      </button>
      <p>{{ $t('保留到这条回复的对话，原对话不变。文件仍共享，审批授权不继承。') }}</p>
      <p
        v-if="disabled"
        role="status"
      >
        {{ $t('当前任务结束后可分叉。') }}
      </p>
      <p
        v-if="error"
        class="message-actions__error"
        role="alert"
      >
        {{ error }}
      </p>
    </div>
  </details>
</template>

<style scoped lang="scss">
.message-actions {
  min-width: 0;
  color: var(--ink-2);
  font-size: var(--text-xs);
}
summary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: var(--space-8);
  min-height: var(--space-8);
  border-radius: var(--radius-sm);
  cursor: pointer;
  list-style: none;
  &::-webkit-details-marker {
    display: none;
  }
  &:hover {
    background: var(--paper-2);
    color: var(--ink);
  }
}
.message-actions__options {
  // Stay in flow: the menu must remain reachable in a narrow, scrollable transcript.
  width: min(100%, var(--measure-304px));
  padding: var(--space-3);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-md);
  background: var(--paper-2);
}
button {
  width: 100%;
  min-height: var(--space-8);
  padding: var(--space-2) var(--space-3);
  text-align: start;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--ink);
  font: inherit;
  &:hover:not(:disabled) {
    background: var(--paper-3);
  }
}
p {
  margin: var(--space-2) var(--space-3);
  overflow-wrap: anywhere;
}
.message-actions__error {
  color: var(--seal);
}
@media (hover: hover) and (pointer: fine) {
  summary {
    opacity: 0.55;
  }
  .message-actions:hover summary,
  .message-actions:focus-within summary,
  .message-actions[open] summary {
    opacity: 1;
  }
}
@media (pointer: coarse) {
  summary,
  button {
    min-height: var(--measure-46px);
  }
  summary {
    min-width: var(--measure-46px);
  }
}
</style>
