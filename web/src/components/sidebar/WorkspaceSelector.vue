<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useDialog } from '@/composables/useDialog'
import { desktopEffects } from '@/composables/useElectron'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkButton from '@/components/common/InkButton.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import WorkspaceFolderBrowser from './WorkspaceFolderBrowser.vue'
import WorkspaceDeleteButton from './WorkspaceDeleteButton.vue'

const props = defineProps<{ collapsed: boolean; presentationTrigger?: boolean }>()
const workspace = useWorkspaceStore(),
  router = useRouter()
const open = ref(false),
  dialog = ref<HTMLDialogElement | null>(null)
const view = ref<'workspaces' | 'add'>('workspaces')
const root = ref(''),
  name = ref(''),
  error = ref('')
const busy = ref(false),
  choosing = ref(false)
const triggerLabel = computed(() =>
  props.presentationTrigger || props.collapsed ? '选择工作区' : '添加文件夹',
)
useDialog(dialog, () => open.value)
onMounted(() => {
  void workspace.load()
})

async function select(id: string) {
  if (busy.value || choosing.value) return
  busy.value = true
  error.value = ''
  try {
    await router.push('/chat')
    workspace.select(id)
    open.value = false
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}
async function add() {
  if (busy.value || choosing.value || !root.value.trim()) return
  busy.value = true
  error.value = ''
  try {
    const folder = await workspace.add(root.value.trim(), name.value.trim())
    await router.push('/chat')
    workspace.select(folder.id)
    open.value = false
    root.value = name.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}
async function choose() {
  if (!desktopEffects || choosing.value || busy.value) return
  choosing.value = true
  error.value = ''
  try {
    const selected = await desktopEffects.chooseWorkspaceFolder()
    if (selected) root.value = selected
    else if (!root.value) open.value = false
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    choosing.value = false
  }
}
function openAdd() {
  view.value = 'add'
  root.value = name.value = error.value = ''
  open.value = true
  if (desktopEffects) void choose()
}
function openSelector() {
  error.value = ''
  view.value = 'workspaces'
  open.value = true
}
function close() {
  if (!busy.value && !choosing.value) open.value = false
}
</script>

<template>
  <div
    class="workspace-selector"
    :class="{ 'presentation-trigger': presentationTrigger }"
  >
    <span
      v-if="!collapsed && !presentationTrigger"
      class="workspace-label"
    >
      {{ $t('工作区') }}
      <small>{{ workspace.folders.length }}</small>
    </span>
    <button
      class="icon-button"
      :aria-label="$t(triggerLabel)"
      :title="$t(triggerLabel)"
      :disabled="choosing || busy"
      :aria-busy="choosing || busy || undefined"
      @click="presentationTrigger || collapsed ? openSelector() : openAdd()"
    >
      <template v-if="presentationTrigger">
        {{ $t('选择工作区') }}
        <IconSymbol
          name="chevron"
          :size="14"
        />
      </template>
      <IconSymbol
        v-else
        :name="collapsed ? 'folder' : 'plus'"
        :size="16"
      />
    </button>
    <p
      v-if="workspace.error && !collapsed"
      class="empty-copy"
      role="alert"
    >
      {{ workspace.error }}
    </p>
    <Teleport to="body">
      <dialog
        ref="dialog"
        class="workspace-dialog"
        :aria-label="$t(view === 'add' ? '添加文件夹' : '选择工作区')"
        @cancel.prevent="close"
        @click.self="close"
      >
        <section>
          <header>
            <strong>{{ $t(view === 'add' ? '添加文件夹' : '选择工作区') }}</strong>
            <button
              type="button"
              class="icon-button"
              :disabled="busy || choosing"
              :aria-label="$t('关闭')"
              @click="close"
            >
              <IconSymbol
                name="close"
                :size="16"
              />
            </button>
          </header>
          <template v-if="view === 'workspaces'">
            <InkLoader v-if="workspace.loading" />
            <p
              v-if="workspace.error"
              class="error-banner"
              role="alert"
            >
              {{ workspace.error }}
              <button
                type="button"
                class="secondary-button"
                @click="workspace.load"
              >
                {{ $t('重试') }}
              </button>
            </p>
            <nav
              class="workspace-options"
              :aria-label="$t('工作区')"
            >
              <div
                v-for="folder in workspace.folders"
                :key="folder.id"
                class="workspace-option-row"
              >
                <button
                  type="button"
                  class="workspace-option"
                  :class="{ selected: workspace.selectedId === folder.id }"
                  :aria-pressed="workspace.selectedId === folder.id"
                  :disabled="busy || choosing"
                  @click="select(folder.id)"
                >
                  <IconSymbol
                    name="folder"
                    :size="18"
                  />
                  <span>
                    <strong>{{ folder.id ? folder.name : $t('默认工作区') }}</strong>
                    <small>{{ folder.root_path }}</small>
                  </span>
                  <IconSymbol
                    v-if="workspace.selectedId === folder.id"
                    name="check"
                    :size="16"
                  />
                </button>
                <WorkspaceDeleteButton
                  v-if="folder.id"
                  :folder="folder"
                  :disabled="busy || choosing"
                />
              </div>
            </nav>
            <p
              v-if="!workspace.folders.length && !workspace.loading && !workspace.error"
              class="empty-copy"
            >
              {{ $t('选择一个文件夹作为工作区。') }}
            </p>
            <footer>
              <InkButton
                tone="primary"
                :disabled="busy || choosing"
                @click="openAdd"
              >
                <IconSymbol
                  name="plus"
                  :size="16"
                />
                {{ $t('添加文件夹') }}
              </InkButton>
            </footer>
          </template>
          <template v-else>
            <template v-if="desktopEffects">
              <div
                v-if="root"
                class="chosen-folder"
              >
                <IconSymbol
                  name="folder"
                  :size="18"
                />
                <span>{{ root }}</span>
              </div>
              <InkButton
                class="folder-picker"
                :disabled="busy || choosing"
                :loading="choosing"
                @click="choose"
              >
                <IconSymbol
                  name="folder"
                  :size="16"
                />
                {{ choosing ? $t('正在选择…') : root ? $t('重新选择文件夹') : $t('选择文件夹') }}
              </InkButton>
            </template>
            <WorkspaceFolderBrowser
              v-else-if="open"
              :initial-path="workspace.current?.root_path"
              :disabled="busy"
              @select="root = $event"
            />
            <form @submit.prevent="add">
              <label class="folder-name">
                {{ $t('工作区名称') }}
                <input
                  v-model="name"
                  maxlength="120"
                  :placeholder="$t('默认使用文件夹名称')"
                  :disabled="busy || choosing"
                />
              </label>
              <footer>
                <InkButton
                  :disabled="busy || choosing"
                  @click="close"
                >
                  {{ $t('取消') }}
                </InkButton>
                <button
                  class="primary-button"
                  :disabled="busy || choosing || !root.trim()"
                  :aria-busy="busy || undefined"
                >
                  {{ busy ? $t('正在添加…') : $t('添加工作区') }}
                </button>
              </footer>
            </form>
          </template>
          <p
            v-if="error"
            role="alert"
            class="error-banner"
          >
            {{ error }}
          </p>
        </section>
      </dialog>
    </Teleport>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/workspace-selector';
</style>
