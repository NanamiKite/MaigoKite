<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { api, type WorkspaceFolderListing } from '@/api/client'
import { GatewayError } from '@/api/http'
import { t } from '@/i18n'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'

const props = defineProps<{ initialPath?: string; disabled?: boolean }>()
const emit = defineEmits<{ select: [path: string] }>()
const listing = shallowRef<WorkspaceFolderListing | null>(null)
const location = ref(''),
  selected = ref(''),
  query = ref(''),
  error = ref('')
const loading = ref(false),
  showHidden = ref(false)
let active: AbortController | undefined
let requestedPath = props.initialPath || ''
const entries = computed(() =>
  (listing.value?.entries || [])
    .filter((item) =>
      item.name.toLocaleLowerCase().includes(query.value.toLocaleLowerCase().trim()),
    )
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name)),
)

async function load(path: string) {
  if (props.disabled) return
  active?.abort()
  const controller = new AbortController()
  active = controller
  requestedPath = path
  loading.value = true
  error.value = ''
  selected.value = ''
  emit('select', '')
  try {
    const result = await api.browseWorkspaceFolders(path, showHidden.value, controller.signal)
    if (controller.signal.aborted || active !== controller) return
    listing.value = result
    location.value = result.path
    query.value = ''
    // Only a successful directory listing supplies the path used for registration.
    selected.value = result.path
    emit('select', result.path)
  } catch (reason) {
    if (controller.signal.aborted || active !== controller) return
    const code = reason instanceof GatewayError ? reason.code : ''
    error.value =
      code === 'LOCAL_ONLY'
        ? t('只能在运行应用的电脑上选择文件夹。')
        : code === 'INVALID_PATH'
          ? t('这个目录位置无效。')
          : code === 'NOT_DIRECTORY'
            ? t('文件夹不存在或没有访问权限。')
            : code === 'READ_FAILED'
              ? t('无法读取这个文件夹。')
              : reason instanceof Error
                ? reason.message
                : String(reason)
  } finally {
    if (active === controller) loading.value = false
  }
}
function select(path: string) {
  if (props.disabled || loading.value) return
  selected.value = path
  emit('select', path)
}
watch(showHidden, () => {
  void load(listing.value?.path || requestedPath)
})
onMounted(() => {
  void load(requestedPath)
})
onBeforeUnmount(() => {
  active?.abort()
})
</script>

<template>
  <section
    class="folder-browser"
    :aria-label="$t('选择文件夹')"
    :aria-busy="loading"
  >
    <form
      class="folder-location"
      @submit.prevent="load(location.trim())"
    >
      <button
        type="button"
        class="icon-button"
        :disabled="disabled || loading || !listing?.parent"
        :aria-label="$t('上一级文件夹')"
        :title="$t('上一级文件夹')"
        @click="load(listing?.parent || '')"
      >
        <IconSymbol
          name="arrow-up"
          :size="16"
        />
      </button>
      <input
        v-model="location"
        maxlength="4096"
        :disabled="disabled || loading"
        :aria-label="$t('目录位置')"
        :placeholder="$t('目录位置')"
      />
      <button
        class="icon-button"
        :disabled="disabled || loading || !location.trim()"
        :aria-label="$t('打开目录')"
        :title="$t('打开目录')"
      >
        <IconSymbol
          name="chevron"
          :size="16"
        />
      </button>
    </form>
    <nav
      class="folder-shortcuts"
      :aria-label="$t('文件夹位置')"
    >
      <button
        type="button"
        class="secondary-button"
        :disabled="disabled || loading"
        @click="load(listing?.home || '')"
      >
        <IconSymbol
          name="home"
          :size="14"
        />
        {{ $t('用户文件夹') }}
      </button>
      <button
        v-if="listing?.root"
        type="button"
        class="secondary-button"
        :disabled="disabled || loading"
        @click="load(listing.root)"
      >
        {{ $t('磁盘根目录') }}
      </button>
      <label>
        <input
          v-model="showHidden"
          type="checkbox"
          :disabled="disabled || loading"
        />
        {{ $t('隐藏文件夹') }}
      </label>
    </nav>
    <input
      v-model="query"
      type="search"
      maxlength="200"
      :disabled="disabled || loading || !!error"
      :aria-label="$t('筛选文件夹')"
      :placeholder="$t('筛选文件夹')"
    />
    <InkLoader v-if="loading" />
    <div
      v-if="error"
      class="folder-error"
      role="alert"
    >
      <p>{{ error }}</p>
      <button
        type="button"
        class="secondary-button"
        :disabled="disabled || loading"
        @click="load(requestedPath)"
      >
        {{ $t('重试') }}
      </button>
    </div>
    <div
      v-else
      class="folder-entries"
      :aria-label="$t('文件夹')"
    >
      <div
        v-for="entry in entries"
        :key="entry.path"
        class="folder-row"
        :class="{ selected: selected === entry.path }"
      >
        <button
          type="button"
          class="folder-entry"
          :aria-pressed="selected === entry.path"
          :disabled="disabled || loading"
          :title="entry.path"
          @click="select(entry.path)"
          @dblclick="load(entry.path)"
          @keydown.enter.prevent="load(entry.path)"
        >
          <IconSymbol
            name="folder"
            :size="18"
          />
          <span>{{ entry.name }}</span>
          <IconSymbol
            v-if="selected === entry.path"
            name="check"
            :size="16"
          />
        </button>
        <button
          type="button"
          class="icon-button folder-enter"
          :disabled="disabled || loading"
          :aria-label="$t('打开文件夹：{0}', [entry.name])"
          :title="$t('打开文件夹：{0}', [entry.name])"
          @click="load(entry.path)"
        >
          <IconSymbol
            name="chevron"
            :size="14"
          />
        </button>
      </div>
      <p
        v-if="!entries.length && !loading"
        class="empty-copy"
      >
        {{ query.trim() ? $t('没有匹配的文件夹') : $t('没有子文件夹，可以选择当前文件夹。') }}
      </p>
    </div>
    <p
      v-if="listing?.truncated && !error"
      class="empty-copy"
    >
      {{ $t('目录内容较多，仅显示部分文件夹。可在目录位置中打开目标文件夹。') }}
    </p>
    <p
      v-if="selected && !loading && !error"
      class="folder-selection"
      role="status"
    >
      <span>{{ $t('已选文件夹') }}</span>
      <strong>{{ selected }}</strong>
    </p>
  </section>
</template>

<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.folder-browser {
  display: grid;
  gap: var(--space-3);
}
.folder-location,
.folder-shortcuts {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.folder-location input {
  flex: 1;
  min-width: 0;
  font: var(--text-xs) var(--font-mono);
}
.folder-shortcuts {
  flex-wrap: wrap;
}
.folder-shortcuts button {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
}
.folder-shortcuts label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
  font-size: var(--text-xs);
  color: var(--ink-2);
}
.folder-shortcuts input {
  width: auto;
}
.folder-entries {
  min-height: var(--measure-100px);
  max-height: min(var(--measure-240px), 30dvh);
  overflow-y: auto;
  border-block: var(--hairline) solid var(--rule);
  padding-block: var(--space-2);
}
.folder-row {
  display: flex;
  align-items: center;
  border-radius: var(--radius-sm);
}
.folder-entry {
  @include mixins.interactive-row;
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  gap: var(--space-3);
  min-height: var(--titlebar-button-size);
  padding: var(--space-2) var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--transparent);
  color: var(--ink);
  text-align: left;
}
.folder-entry > span {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.folder-row.selected {
  background: var(--accent-selected);
}
.folder-row.selected .folder-entry > .icon-symbol:last-child {
  color: var(--accent);
}
.folder-enter {
  flex: none;
  color: var(--ink-2);
}
.folder-selection {
  display: grid;
  gap: var(--space-1);
  margin: 0;
  font-size: var(--text-xs);
}
.folder-selection span {
  color: var(--ink-2);
}
.folder-selection strong {
  overflow-wrap: anywhere;
  font: var(--text-xs) var(--font-mono);
}
.folder-error {
  padding: var(--space-3);
  color: var(--seal);
}
.folder-error p {
  margin: 0 0 var(--space-3);
}
</style>
