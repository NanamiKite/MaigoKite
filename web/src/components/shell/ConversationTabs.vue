<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useChatViewStore } from '@/stores/chat-view'
import { useWorkspaceStore } from '@/stores/workspace'
import { useInboxStore } from '@/stores/inbox'
import { useRunStore } from '@/stores/run'
import { pendingItem } from '@/utils/rail-presentation'
import { t } from '@/i18n'
import Pulse from './Pulse.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'

const route = useRoute(),
  router = useRouter(),
  session = useSessionStore()
const view = useChatViewStore(),
  workspace = useWorkspaceStore()
const inbox = useInboxStore(),
  run = useRunStore()
const strip = ref<HTMLElement | null>(null)
const editing = ref<string | null>(null),
  name = ref(''),
  busy = ref(false),
  error = ref('')
const active = computed(() => String(route.params.id || ''))
const pending = computed(
  () => new Set(inbox.items.filter(pendingItem).map((item) => item.sessionId)),
)
const tabs = computed(() => {
  const items = new Map(session.sessions.map((item) => [item.id, item]))
  const result = view.opened.map((id) => {
    const item = items.get(id)
    return {
      id,
      title: item?.title || t('对话'),
      folder:
        workspace.folders.find((folder) => folder.id === (item?.folder_id || ''))?.name ||
        t('默认工作区'),
      state: pending.value.has(id) ? ('waiting' as const) : run.pulse(id),
    }
  })
  if (!active.value)
    result.push({
      id: '',
      title: t('新页'),
      folder: workspace.current?.name || t('默认工作区'),
      state: 'idle',
    })
  return result
})
function reveal() {
  strip.value
    ?.querySelector('[aria-selected="true"]')
    ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
}
watch(
  active,
  (id) => {
    view.open(id)
    editing.value = null
    error.value = ''
    void nextTick(reveal)
  },
  { immediate: true },
)
watch(
  () => session.sessions.map((item) => item.id),
  (next, previous = []) => {
    for (const id of previous) if (!next.includes(id)) view.forget(id)
    if (!session.isLoading && next.length)
      for (const id of view.opened) if (id !== active.value && !next.includes(id)) view.forget(id)
  },
  { immediate: true },
)
async function select(id: string, focus = false) {
  await router.push(id ? '/chat/' + id : '/chat')
  await nextTick()
  if (focus)
    strip.value
      ?.querySelector<HTMLElement>('[aria-selected="true"]')
      ?.focus({ preventScroll: true })
}
async function close(id: string) {
  const index = view.opened.indexOf(id)
  const next = view.opened[index + 1] || view.opened[index - 1] || ''
  // Navigate first: the outgoing view gets to retain its draft and scroll anchor.
  if (active.value === id) await select(next, true)
  view.close(id)
  if (editing.value === id) editing.value = null
}
async function rename(id: string, title: string) {
  if (!id || busy.value) return
  editing.value = id
  name.value = title
  error.value = ''
  await nextTick()
  strip.value?.querySelector<HTMLInputElement>('input')?.select()
}
async function save() {
  const id = editing.value,
    title = name.value.trim()
  if (!id || !title || busy.value) return
  busy.value = true
  try {
    await session.manageSession(id, { title })
    if (editing.value === id) editing.value = null
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}
function key(event: KeyboardEvent, index: number) {
  if (event.target instanceof HTMLInputElement) return
  const item = tabs.value[index]
  if (event.key === 'F2') {
    event.preventDefault()
    void rename(item.id, item.title)
  } else if (event.key === 'Delete') {
    event.preventDefault()
    if (item.id || view.opened.length) void close(item.id)
  } else if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? tabs.value.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.value.length) % tabs.value.length
    void select(tabs.value[next].id, true)
  }
}
</script>
<template>
  <div class="conversation-tabs">
    <div
      ref="strip"
      class="conversation-tabs__strip"
      role="tablist"
      :aria-label="$t('已打开的对话')"
    >
      <div
        v-for="(tab, index) in tabs"
        :key="tab.id"
        class="conversation-tab"
        :class="{ active: active === tab.id }"
      >
        <form
          v-if="editing === tab.id"
          class="conversation-tab__rename"
          @submit.prevent="save"
        >
          <input
            v-model="name"
            maxlength="200"
            :disabled="busy"
            :aria-label="$t('对话名称')"
            @blur="save"
            @keydown.esc.stop.prevent="editing = null"
          />
        </form>
        <button
          v-else
          type="button"
          role="tab"
          class="conversation-tab__select"
          :aria-selected="active === tab.id"
          :tabindex="active === tab.id ? 0 : -1"
          :aria-label="
            tab.title + ' · ' + tab.folder + (tab.state === 'waiting' ? ' · ' + $t('等你处理') : '')
          "
          :title="tab.title + ' · ' + tab.folder"
          aria-controls="chat-page"
          @click="select(tab.id)"
          @dblclick="rename(tab.id, tab.title)"
          @keydown="key($event, index)"
        >
          <Pulse
            v-if="tab.state !== 'idle'"
            :state="tab.state"
            :size="12"
          />
          <span>{{ tab.title }}</span>
        </button>
        <button
          v-if="tab.id || view.opened.length"
          type="button"
          class="conversation-tab__close"
          :aria-label="$t('关闭标签：{0}', [tab.title])"
          :title="$t('关闭标签，不会删除对话或停止任务')"
          @click.stop="close(tab.id)"
          @keydown="key($event, index)"
        >
          <IconSymbol
            name="close"
            :size="14"
          />
        </button>
      </div>
    </div>
    <button
      class="conversation-tabs__new"
      type="button"
      :aria-label="$t('新页')"
      :title="$t('新页')"
      @click="select('')"
    >
      <IconSymbol
        name="plus"
        :size="18"
      />
    </button>
    <span
      v-if="error"
      class="conversation-tabs__error"
      role="alert"
    >
      {{ error }}
    </span>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/conversation-tabs';
</style>
