<script setup lang="ts">
import { t } from '@/i18n'
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useUIStore } from '@/stores/ui'
import { useDeskStore } from '@/stores/desk'
import { useSessionStore } from '@/stores/session'
import { useMemoryStore } from '@/stores/memory'
import Kbd from './Kbd.vue'
import IconSymbol from './IconSymbol.vue'
import { useDialog } from '@/composables/useDialog'
type Group = 'session' | 'navigation' | 'action' | 'settings'
type Entry = {
  id: string
  label: string
  group: Group
  keywords?: string
  to?: string
  perform?: () => void
}
const ui = useUIStore(),
  desk = useDeskStore(),
  session = useSessionStore(),
  memory = useMemoryStore(),
  router = useRouter()
const query = ref(''),
  selected = ref(0),
  input = ref<HTMLInputElement | null>(null),
  dialog = ref<HTMLDialogElement | null>(null)
const storageKey = (import.meta.env.VITE_MOCK === '1' ? 'demo.' : '') + 'maigokite.commands.recent'
const recent = ref<string[]>([])
try {
  const value: unknown = JSON.parse(localStorage.getItem(storageKey) || '[]')
  if (Array.isArray(value))
    recent.value = value.filter((id): id is string => typeof id === 'string').slice(0, 12)
} catch {
  /* Preferences are optional. */
}
useDialog(dialog, () => ui.commandOpen || ui.shortcutsOpen)
let opener: HTMLElement | null = null
function close() {
  ui.commandOpen = ui.shortcutsOpen = false
}
const mod = /Mac/.test(navigator.platform) ? '⌘' : 'Ctrl'
const shortcuts = computed(() => [
  [mod + '+K', '万能面板'],
  [mod + '+N', '新页'],
  [mod + '+B', '侧栏'],
  [mod + '+.', '抽屉'],
  [mod + '+J', '等你处理'],
  [mod + '+/', '快捷键表'],
  ['Alt+S', session.isSending ? '加入队列' : '发送消息'],
  [mod + '+Enter', session.isSending ? '加入队列' : '发送消息'],
  ['Enter', ui.appearance.enterBehavior === 'send'
    ? ui.appearance.confirmEnterSend
      ? session.isSending ? '确认加入队列' : '确认发送'
      : session.isSending ? '加入队列' : '发送消息'
    : '换行'],
  ['Shift+Enter', '换行'],
  ['[ / ]', '跳转墨线节点'],
  ['Y / N', '允许 / 驳回'],
  ['Esc', '中断 / 关闭浮层'],
])
const order: Group[] = ['session', 'navigation', 'action', 'settings']
const groupLabels: Record<Group, string> = {
  session: '会话',
  navigation: '跳转',
  action: '操作',
  settings: '设置',
}
const entries = computed<Entry[]>(() => [
  ...session.sessions.map((item) => ({
    id: 'session:' + item.id,
    label: item.title || t('未命名对话'),
    to: '/chat/' + item.id,
    group: 'session' as const,
  })),
  ...memory.items.map((item) => ({
    id: 'memory:' + item.id,
    label: item.content,
    to: '/memory?focus=' + encodeURIComponent(item.id),
    group: 'navigation' as const,
  })),
  ...[
    ['new', '新页', '/chat', 'xy new chat'],
    ['memory', '它记得', '/memory', 'tjd memory'],
    ['overview', '工作记录', '/overview', 'gzjl overview records'],
    ['integrations', '扩展', '/integrations', 'kz integrations'],
    ['audit', '审计日志', '/audit', 'sjrz audit'],
    ['data', '数据概览', '/data', 'sjgl data'],
  ].map(([id, label, to, keywords]) => ({
    id,
    label: t(label),
    to,
    keywords,
    group: 'navigation' as const,
  })),
  ...[
    ['pending', '等你处理', 'dncl pending'],
    ['files', '工作区文件', 'gzqwj workspace files'],
    ['changes', '查看变更', 'ckbg changes'],
    ['plan', '查看计划', 'ckjh plan'],
  ].map(([id, label, keywords]) => ({
    id,
    label: t(label),
    keywords,
    group: 'action' as const,
    perform: () => ui.openDrawer(id as 'pending' | 'files' | 'changes' | 'plan'),
  })),
  { id: 'settings', label: t('设置'), to: '/settings', keywords: 'sz settings', group: 'settings' },
  {
    id: 'desk-background', label: t('切换案头背景'), keywords: 'atbj background wallpaper', group: 'settings',
    perform: () => {
      if (desk.image) desk.change({ on: !desk.preferences.on })
      else void router.push('/settings?tab=general&field=desk-background')
    },
  },
  {
    id: 'theme',
    label: t(ui.theme === 'dark' ? '切换到宣纸' : '切换到松烟'),
    keywords: 'zt theme light dark',
    group: 'settings',
    perform: () => ui.setAppearance({ theme: ui.theme === 'dark' ? 'light' : 'dark' }),
  },
  {
    id: 'texture',
    label: t(ui.appearance.texture ? '关闭纸纹' : '打开纸纹'),
    keywords: 'zw texture paper',
    group: 'settings',
    perform: () => ui.setAppearance({ texture: !ui.appearance.texture }),
  },
])
function subsequence(label: string, needle: string): number[] | undefined {
  const chars = Array.from(label.toLocaleLowerCase()),
    wanted = Array.from(needle),
    hits: number[] = []
  let cursor = 0
  for (const char of wanted) {
    const index = chars.indexOf(char, cursor)
    if (index < 0) return undefined
    hits.push(index)
    cursor = index + 1
  }
  return hits
}
function match(entry: Entry) {
  const needle = query.value.toLocaleLowerCase().replace(/\s/g, '')
  if (!needle) return { hits: [] as number[], score: 0 }
  const hits = subsequence(entry.label, needle)
  const words = entry.label.match(/[A-Za-z0-9]+/g) || []
  const initials = words
    .map((word) => word[0])
    .join('')
    .toLocaleLowerCase()
  if (hits)
    return {
      hits,
      score: entry.label.toLocaleLowerCase().includes(needle)
        ? 100
        : 60 - (hits[hits.length - 1] - hits[0]),
    }
  if (initials && subsequence(initials, needle)) {
    const starts: number[] = []
    let offset = 0
    for (const word of words) {
      const index = entry.label.indexOf(word, offset)
      starts.push(Array.from(entry.label.slice(0, index)).length)
      offset = index + word.length
    }
    return { hits: (subsequence(initials, needle) || []).map((index) => starts[index]), score: 80 }
  }
  if (entry.keywords?.toLocaleLowerCase().includes(needle))
    return { hits: [] as number[], score: 40 }
}
const groups = computed(() =>
  order
    .map((group) => ({
      id: group,
      label: groupLabels[group],
      items: entries.value
        .filter((entry) => entry.group === group)
        .flatMap((entry) => {
          const matched = match(entry)
          return matched ? [{ ...entry, ...matched }] : []
        })
        .sort(
          (a, b) =>
            b.score - a.score ||
            (recent.value.indexOf(a.id) < 0 ? Infinity : recent.value.indexOf(a.id)) -
              (recent.value.indexOf(b.id) < 0 ? Infinity : recent.value.indexOf(b.id)),
        )
        .slice(0, query.value ? 20 : 8),
    }))
    .filter((group) => group.items.length),
)
const items = computed(() => groups.value.flatMap((group) => group.items))
function segments(label: string, hits: number[]) {
  const marked = new Set(hits)
  return Array.from(label).map((text, index) => ({ text, hit: marked.has(index) }))
}
watch(
  () => ui.commandOpen || ui.shortcutsOpen,
  async (open) => {
    if (open) {
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
      query.value = ''
      selected.value = 0
      if (ui.commandOpen && !memory.items.length) void memory.load()
      await nextTick()
      if (ui.commandOpen) input.value?.focus()
    } else {
      await nextTick()
      if (opener?.isConnected && !opener.closest('[inert]')) opener.focus({ preventScroll: true })
    }
  },
)
watch(query, () => {
  selected.value = 0
})
watch(items, (value) => {
  selected.value = Math.max(0, Math.min(selected.value, value.length - 1))
})
watch(selected, async () => {
  await nextTick()
  dialog.value?.querySelector('.selected')?.scrollIntoView({ block: 'nearest' })
})
function choose(entry: Entry) {
  recent.value = [entry.id, ...recent.value.filter((id) => id !== entry.id)].slice(0, 12)
  try {
    localStorage.setItem(storageKey, JSON.stringify(recent.value))
  } catch {
    /* Optional. */
  }
  close()
  if (entry.to) void router.push(entry.to)
  else entry.perform?.()
}
function onKey(event: KeyboardEvent) {
  if (event.isComposing || !ui.commandOpen) return
  if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault()
    if (items.value.length)
      selected.value =
        (selected.value + (event.key === 'ArrowDown' ? 1 : -1) + items.value.length) %
        items.value.length
  } else if (event.key === 'Enter' && items.value[selected.value]) {
    event.preventDefault()
    choose(items.value[selected.value])
  } else if (event.key === 'Tab' && groups.value.length) {
    event.preventDefault()
    const group = groups.value.findIndex((entry) => entry.id === items.value[selected.value]?.group)
    const next =
      groups.value[(group + (event.shiftKey ? -1 : 1) + groups.value.length) % groups.value.length]
    selected.value = items.value.findIndex((entry) => entry.group === next.id)
  }
}
</script>
<template>
  <dialog
    ref="dialog"
    class="palette-shade"
    :aria-label="ui.commandOpen ? $t('万能面板') : $t('快捷键表')"
    @cancel.prevent="close"
    @keydown="onKey"
    @keydown.esc.stop
    @click.self="close"
  >
    <section
      v-if="ui.commandOpen"
      class="palette"
    >
      <header class="palette-search">
        <IconSymbol
          name="search"
          :size="20"
        />
        <input
          ref="input"
          v-model="query"
          :placeholder="$t('找会话、页面或操作…')"
          :aria-label="$t('搜索')"
          role="combobox"
          aria-controls="palette-results"
          aria-autocomplete="list"
          aria-expanded="true"
          :aria-activedescendant="items[selected] ? 'command-' + items[selected].id : undefined"
        ><Kbd keys="Esc" />
      </header>
      <div
        id="palette-results"
        class="results"
        role="listbox"
        :aria-label="$t('搜索结果')"
      >
        <section
          v-for="group in groups"
          :key="group.id"
          class="result-group"
          role="group"
          :aria-label="$t(group.label)"
        >
          <h3>{{ $t(group.label) }}</h3>
          <button
            v-for="item in group.items"
            :id="'command-' + item.id"
            :key="item.id"
            role="option"
            :aria-selected="items[selected]?.id === item.id"
            :class="{ selected: items[selected]?.id === item.id }"
            @pointermove="selected = items.findIndex((entry) => entry.id === item.id)"
            @click="choose(item)"
          >
            <span class="result-label"><template
              v-for="(part, index) in segments(item.label, item.hits)"
              :key="index"
            ><mark v-if="part.hit">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></span>
            <small v-if="recent.includes(item.id)">{{ $t('最近使用') }}</small>
            <IconSymbol
              v-if="items[selected]?.id === item.id"
              name="chevron"
              :size="14"
            />
          </button>
        </section>
        <p
          v-if="!items.length"
          class="palette-empty"
        >
          {{ $t('没有找到相关内容。') }}
        </p>
      </div>
      <footer>{{ $t('↑↓ 选择 · Tab 切换分组 · Enter 打开') }}</footer>
    </section>
    <section
      v-else
      class="palette shortcut-sheet"
    >
      <header>
        {{ $t('快捷键')
        }}<button
          class="icon-button"
          :aria-label="$t('关闭快捷键表')"
          @click="close"
        >
          <IconSymbol
            name="close"
            :size="18"
          />
        </button>
      </header>
      <p
        v-for="[keys, label] in shortcuts"
        :key="keys"
      >
        <span>{{ $t(label) }}</span><Kbd :keys="keys" />
      </p>
    </section>
  </dialog>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.palette-shade {
  position: fixed;
  inset: 0;
  z-index: var(--z-palette);
  margin: 0;
  border: 0;
  width: 100%;
  height: 100%;
  max-width: none;
  max-height: none;
  color: var(--ink);
  background: var(--transparent);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: var(--palette-top) var(--space-5) var(--space-5);
}
.palette-shade:not([open]) {
  display: none;
}
.palette-shade::backdrop {
  background: var(--backdrop);
}
.palette {
  position: relative;
  isolation: isolate;
  width: min(var(--palette-width), 100%);
  overflow: hidden;
  border: var(--hairline) solid var(--highlight);
  border-radius: var(--radius-md);
  background: var(--paper-raised);
  box-shadow: var(--shadow-float);
  backdrop-filter: var(--float-blur);
  animation: palette-arrive var(--motion-panel) var(--ease-out);
}
.palette::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: var(--z-underlay);
  pointer-events: none;
  opacity: var(--noise-float-opacity);
  background-image: var(--noise);
}
.palette-search {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-5);
  border-bottom: var(--hairline) solid var(--rule);
  color: var(--ink-2);
}
.palette-search input {
  flex: 1;
  min-width: 0;
  width: 100%;
  padding: 0;
  border: 0;
  outline: none;
  background: var(--transparent);
  font-size: var(--type-body);
}
.results {
  max-height: var(--palette-height);
  overflow: auto;
  padding: var(--space-3);
}
.result-group h3 {
  padding: var(--space-3) var(--space-4);
  margin: 0;
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  letter-spacing: var(--tracking-label);
  color: var(--ink-2);
}
.result-group button {
  @include mixins.interactive-row;
  display: flex;
  width: 100%;
  align-items: center;
  gap: var(--space-4);
  min-height: var(--titlebar-button-size);
  padding: var(--space-3) var(--space-4);
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--transparent);
  color: var(--ink);
  text-align: left;
  font-size: var(--text-sm);
}
button.selected {
  background: var(--accent-selected);
}
.result-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
mark {
  @include mixins.text-ink;
  background: var(--transparent);
  font-weight: var(--weight-semibold);
}
small {
  color: var(--ink-2);
  font-size: var(--text-xs);
  white-space: nowrap;
}
footer {
  padding: var(--space-3) var(--space-5);
  border-top: var(--hairline) solid var(--rule);
  font-size: var(--text-xs);
  color: var(--ink-2);
}
.palette-empty {
  padding: var(--space-7);
  color: var(--ink-2);
  font-size: var(--text-sm);
}
.shortcut-sheet {
  padding: var(--space-6);
}
.shortcut-sheet header,
.shortcut-sheet p {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-5);
}
.shortcut-sheet p {
  color: var(--ink-2);
  font-size: var(--text-sm);
}
@keyframes palette-arrive {
  from {
    opacity: 0;
    transform: translateY(calc(-1 * var(--space-4))) scale(var(--press-scale));
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
</style>
