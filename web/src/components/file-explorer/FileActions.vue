<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { api, type WorkspaceFileEntry } from '@/api/client'
import { GatewayError } from '@/api/http'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { desktopEffects } from '@/composables/useElectron'
import { useDialog } from '@/composables/useDialog'
import IconSymbol from '@/components/common/IconSymbol.vue'
import Toolbar from './Toolbar.vue'
const props = defineProps<{ parent: string; loading?: boolean }>()
const emit = defineEmits<{ refresh: []; created: [entry: WorkspaceFileEntry] }>()
const workspace = useWorkspaceStore()
const kind = ref<'file' | 'directory' | null>(null),
  name = ref(''),
  error = ref(''),
  saved = ref('')
const busy = ref(false),
  opening = ref(false),
  dialog = ref<HTMLDialogElement | null>(null)
const location = computed(() => props.parent || workspace.current?.name || t('工作区'))
let active = true
useDialog(dialog, () => Boolean(kind.value))
watch(
  () => workspace.selectedId,
  () => {
    kind.value = null
    error.value = saved.value = ''
  },
)
function message(reason: unknown) {
  const labels: Record<string, string> = {
    ALREADY_EXISTS: '已有同名文件或文件夹，请换一个名称。',
    INVALID_NAME: '名称不能包含路径分隔符、保留名称或特殊字符。',
    INVALID_PATH: '创建位置不可用，请刷新文件树。',
    NOT_DIRECTORY: '此位置不是可用的文件夹。',
    ACCESS_DENIED: '没有权限在此文件夹中创建内容。',
    CREATE_FAILED: '创建失败，请检查文件夹是否存在以及写入权限。',
    LOCAL_ONLY: '只能在运行应用的本机打开文件夹。',
    OPEN_FAILED: '无法打开文件资源管理器，请检查桌面环境。',
  }
  if (reason instanceof GatewayError && labels[reason.code]) return t(labels[reason.code])
  return reason instanceof Error ? reason.message : String(reason)
}
function begin(type: 'file' | 'directory') {
  kind.value = type
  name.value = error.value = saved.value = ''
}
async function create() {
  const type = kind.value,
    folderId = workspace.selectedId,
    parent = props.parent
  if (!type || busy.value || !name.value.trim()) return
  busy.value = true
  error.value = ''
  try {
    const entry = await api.createWorkspaceEntry(parent, name.value.trim(), type, folderId)
    if (!active || folderId !== workspace.selectedId) return
    kind.value = null
    saved.value = t('已创建 {0}', [entry.name])
    emit('created', entry)
  } catch (reason) {
    if (active && folderId === workspace.selectedId) error.value = message(reason)
  } finally {
    busy.value = false
  }
}
async function openFolder() {
  if (opening.value) return
  const folderId = workspace.selectedId,
    parent = props.parent
  opening.value = true
  error.value = saved.value = ''
  try {
    if (desktopEffects) {
      const result = await desktopEffects.openPath(parent || '.', folderId)
      if (result) throw new Error(result)
    } else await api.openWorkspaceFolder(parent, folderId)
  } catch (reason) {
    if (active && folderId === workspace.selectedId) error.value = message(reason)
  } finally {
    opening.value = false
  }
}
onScopeDispose(() => {
  active = false
})
</script>
<template>
  <Toolbar
    :loading="loading"
    :location="location"
    :opening="opening"
    @refresh="emit('refresh')"
    @create="begin"
    @open-folder="openFolder"
  />
  <p
    v-if="error && !kind"
    class="file-action-status failed"
    role="alert"
  >
    {{ error }}
  </p>
  <p
    v-if="saved"
    class="file-action-status"
    role="status"
  >
    {{ saved }}
  </p>
  <dialog
    ref="dialog"
    class="file-create-dialog"
    :aria-label="kind === 'directory' ? $t('新建文件夹') : $t('新建空文件')"
    @cancel.prevent="!busy && (kind = null)"
  >
    <form @submit.prevent="create">
      <header>
        <IconSymbol
          :name="kind === 'directory' ? 'folder' : 'file'"
          :size="18"
        />
        <h3>{{ kind === 'directory' ? $t('新建文件夹') : $t('新建空文件') }}</h3>
      </header>
      <p class="create-location">
        {{ $t('创建位置：{0}', [location]) }}
      </p>
      <label>
        {{ $t('名称') }}
        <input
          v-model="name"
          autofocus
          required
          maxlength="255"
          :disabled="busy"
          autocomplete="off"
        />
      </label>
      <p
        v-if="error"
        class="failed"
        role="alert"
      >
        {{ error }}
      </p>
      <footer>
        <button
          type="button"
          class="secondary-button"
          :disabled="busy"
          @click="kind = null"
        >
          {{ $t('取消') }}
        </button>
        <button
          type="submit"
          class="primary-button"
          :disabled="busy || !name.trim()"
          :aria-busy="busy || undefined"
        >
          {{ busy ? $t('正在创建…') : $t('创建') }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
<style scoped lang="scss">
.file-action-status {
  flex: none;
  padding: var(--space-2) var(--space-4);
  margin: 0;
  color: var(--ink-2);
  font-size: var(--text-xs);
  overflow-wrap: anywhere;
}
.failed {
  color: var(--seal);
}
.file-create-dialog {
  width: min(var(--session-dialog-width), calc(100vw - var(--space-8)));
  border: var(--hairline) solid var(--rule);
  padding: var(--space-6);
  border-radius: var(--radius-lg);
  background: var(--paper-raised);
  color: var(--ink);
  box-shadow: var(--shadow-float);
}
.file-create-dialog::backdrop {
  background: var(--backdrop);
}
header,
footer {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
h3 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-medium);
}
.create-location {
  color: var(--ink-2);
  overflow-wrap: anywhere;
  font-size: var(--text-xs);
}
label {
  display: grid;
  gap: var(--space-3);
  font-size: var(--text-sm);
}
input {
  width: 100%;
  padding: var(--space-3);
}
footer {
  justify-content: flex-end;
  margin-top: var(--space-5);
}
</style>
