<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { WorkspaceFolder } from '@/api/client'
import { useWorkspaceStore } from '@/stores/workspace'
import { useSessionStore } from '@/stores/session'
import { useRunStore } from '@/stores/run'
import { useDialog } from '@/composables/useDialog'
import { i18n, t } from '@/i18n'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkButton from '@/components/common/InkButton.vue'

const props = defineProps<{ folder: WorkspaceFolder; disabled?: boolean }>()
const workspace = useWorkspaceStore(),
  session = useSessionStore(),
  run = useRunStore()
const open = ref(false),
  busy = ref(false),
  error = ref('')
const dialog = ref<HTMLDialogElement | null>(null),
  trigger = ref<HTMLButtonElement | null>(null)
const conversations = computed(() =>
  session.sessions.filter((item) => item.folder_id === props.folder.id),
)
const blocked = computed(() =>
  conversations.value.some(
    (item) =>
      session.runningTaskIds[item.id]?.length ||
      (run.turns[item.id] || []).some((turn) => ['running', 'waiting'].includes(turn.status)),
  ),
)
useDialog(dialog, () => open.value)

function confirm() {
  error.value = ''
  open.value = true
}
function close() {
  if (!busy.value) open.value = false
}
async function remove() {
  if (busy.value || blocked.value || !props.folder.id) return
  busy.value = true
  error.value = ''
  const container = trigger.value?.closest('.workspace-options, .recent-chats')
  try {
    const id = props.folder.id
    await workspace.remove(id)
    session.moveWorkspaceSessionsToDefault(id)
    open.value = false
    await nextTick()
    // The removed row cannot receive focus back; choose the next available row.
    if (container?.isConnected)
      container.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : String(reason)
    error.value = i18n.global.te(message) ? t(message) : message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <span
    v-if="folder.id"
    class="workspace-delete"
  >
    <button
      ref="trigger"
      type="button"
      class="workspace-delete-trigger"
      :disabled="disabled || busy"
      :aria-label="$t('删除工作区：{0}', [folder.name])"
      :title="$t('删除工作区：{0}', [folder.name])"
      @click="confirm"
    >
      <IconSymbol
        name="trash"
        :size="15"
      />
    </button>
    <Teleport to="body">
      <dialog
        ref="dialog"
        class="workspace-delete-dialog"
        :aria-label="$t('删除工作区？')"
        @cancel.prevent="close"
        @click.self="close"
      >
        <header>
          <strong>{{ $t('删除工作区？') }}</strong>
          <button
            type="button"
            class="icon-button"
            :disabled="busy"
            :aria-label="$t('关闭')"
            @click="close"
          >
            <IconSymbol
              name="close"
              :size="16"
            />
          </button>
        </header>
        <strong class="workspace-delete-name">{{ folder.name }}</strong>
        <p class="workspace-delete-path">{{ folder.root_path }}</p>
        <p>
          {{
            $t(
              '只从应用中移除，磁盘文件不会删除。对话和消息会保留并移到默认工作区，后续文件操作将使用默认目录。',
            )
          }}
        </p>
        <p
          v-if="blocked"
          class="empty-copy"
          role="status"
        >
          {{ $t('工作区中有未结束的任务，请完成或停止后再删除。') }}
        </p>
        <p
          v-if="error"
          class="error-banner"
          role="alert"
        >
          {{ error }}
        </p>
        <footer>
          <InkButton
            :disabled="busy"
            @click="close"
          >
            {{ $t('取消') }}
          </InkButton>
          <InkButton
            tone="danger"
            :disabled="busy || blocked"
            :loading="busy"
            @click="remove"
          >
            {{ busy ? $t('正在删除…') : $t('确认删除') }}
          </InkButton>
        </footer>
      </dialog>
    </Teleport>
  </span>
</template>

<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.workspace-delete {
  display: flex;
  flex: none;
}
.workspace-delete-trigger {
  @include mixins.interactive-row;
  display: grid;
  place-items: center;
  width: var(--size-28);
  height: var(--space-8);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--transparent);
  color: var(--ink-2);
}
.workspace-delete-trigger:not(:disabled):hover {
  color: var(--seal);
  background: var(--seal-soft);
}
.workspace-delete-dialog {
  @include mixins.floating;
  width: min(var(--measure-450px), calc(100vw - var(--space-8)));
  max-height: calc(100dvh - var(--space-8));
  overflow-y: auto;
  padding: var(--space-6);
  color: var(--ink);
}
.workspace-delete-dialog header,
.workspace-delete-dialog footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.workspace-delete-dialog header {
  margin-bottom: var(--space-5);
}
.workspace-delete-dialog footer {
  justify-content: flex-end;
  margin-top: var(--space-5);
}
.workspace-delete-dialog::backdrop {
  background: var(--backdrop);
}
.workspace-delete-name {
  overflow-wrap: anywhere;
}
.workspace-delete-path {
  font: var(--text-xs) var(--font-mono);
  color: var(--ink-2);
  overflow-wrap: anywhere;
}
</style>
