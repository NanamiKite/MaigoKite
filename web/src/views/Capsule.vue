<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/session'
import { useWorkspaceStore } from '@/stores/workspace'
import { useRouter } from 'vue-router'
import Pulse from '@/components/shell/Pulse.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import { desktopEffects } from '@/composables/useElectron'
useUIStore()
const session = useSessionStore(),
  workspace = useWorkspaceStore(),
  router = useRouter()
const text = ref(''),
  destination = ref<'recent' | 'new'>('recent'),
  input = ref<HTMLInputElement | null>(null),
  sending = ref(false),
  error = ref('')
onMounted(async () => {
  await nextTick()
  input.value?.focus()
})
async function send(newSession = false) {
  if (!text.value.trim() || sending.value) return
  sending.value = true
  error.value = ''
  try {
    if (desktopEffects) await desktopEffects.sendCapsule({ content: text.value.trim(), newSession })
    else {
      await session.loadSessions()
      const recent = session.sessions.find(
        (item) => !item.archived && (item.folder_id || '') === workspace.selectedId,
      )
      const id = newSession || !recent ? (await session.createSession('新对话')).id : recent.id
      await session.sendToSession(id, text.value.trim())
      await router.push(`/chat/${id}`)
    }
    text.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    sending.value = false
  }
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key === 'Escape') void window.electronAPI?.windowControl('hide')
  if (event.key === 'Enter') {
    event.preventDefault()
    void send(event.ctrlKey || event.metaKey || destination.value === 'new')
  }
}
function drop(event: DragEvent) {
  const file = event.dataTransfer?.files[0] as (File & { path?: string }) | undefined
  const value = file?.path || event.dataTransfer?.getData('text/plain')
  if (value) text.value += `${text.value ? ' ' : ''}${value}`
}
</script>
<template>
  <form
    class="capsule inkline-shell"
    @submit.prevent="send(destination === 'new')"
    @dragover.prevent
    @drop.prevent="drop"
  >
    <Pulse :state="error ? 'error' : sending ? 'thinking' : 'idle'" />
    <input
      ref="input"
      v-model="text"
      :placeholder="error || $t('输入消息…')"
      maxlength="10000"
      :aria-label="$t('速记内容')"
      :title="error"
      @keydown="keydown"
    />
    <div
      class="capsule-destinations"
      role="group"
      :aria-label="$t('速记发送目标')"
    >
      <button
        type="button"
        :disabled="sending"
        :aria-pressed="destination === 'recent'"
        @click="destination = 'recent'"
      >
        {{ $t('最近会话') }}
      </button>
      <button
        type="button"
        :disabled="sending"
        :aria-pressed="destination === 'new'"
        @click="destination = 'new'"
      >
        {{ $t('新会话') }}
      </button>
    </div>
    <button
      class="icon-button"
      :disabled="!text.trim() || sending"
      :aria-label="$t('发送')"
      :title="$t('Enter 发送 · Ctrl/⌘+Enter 新页')"
    >
      <IconSymbol
        name="arrow-up"
        :size="18"
      />
    </button>
    <span
      class="sr-only"
      :role="error ? 'alert' : 'status'"
    >
      {{ error || (sending ? $t('正在发送…') : '') }}
    </span>
  </form>
</template>
<style scoped lang="scss">
.capsule {
  height: var(--capsule-height);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-5);
  background: var(--paper);
  border: var(--hairline) solid var(--rule);
  box-shadow: var(--elevation-inset);
  animation: paper-arrive var(--motion-panel) var(--ease-out) both;
  border-radius: var(--radius-md);
  -webkit-app-region: drag;
}
input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: 0;
  background: var(--transparent);
  font-size: var(--text-md);
  -webkit-app-region: no-drag;
}
input::placeholder {
  color: var(--ink-3);
}
button {
  border: 0;
  background: var(--transparent);
  color: var(--ink-2);
  padding: var(--space-3);
  -webkit-app-region: no-drag;
}
.capsule-destinations {
  display: flex;
  flex: none;
  gap: var(--space-1);
  padding: var(--space-1);
  background: var(--paper-2);
  border-radius: var(--radius-sm);
  -webkit-app-region: no-drag;
}
.capsule-destinations button {
  border: 0;
  padding: var(--space-2);
  font-size: var(--text-xs);
  background: var(--transparent);
  -webkit-app-region: no-drag;
}
.capsule-destinations button[aria-pressed='true'] {
  background: var(--accent-selected);
  color: var(--accent);
}
@media (prefers-reduced-motion: reduce) {
  .capsule {
    animation: paper-fade var(--motion-reduced) var(--ease-out) both !important;
  }
}
</style>
