<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { t } from '@/i18n'
import type { Turn } from '@/api/events'
import IconSymbol from '@/components/common/IconSymbol.vue'
import SubagentConversation from './SubagentConversation.vue'
const props = defineProps<{ sessionId: string; turns: Turn[] }>()
type ChildTab = { key: string; turnId: string; childId: string; label: string }
const tabs = ref<ChildTab[]>([])
const selected = ref('')
const navigation = ref<HTMLElement | null>(null)
const childPage = ref<HTMLElement | null>(null)
const positions = new Map<string, number>()
const current = computed(() => {
  const tab = tabs.value.find((item) => item.key === selected.value)
  const turn = props.turns.find((item) => item.id === tab?.turnId)
  const child = turn?.subagents?.find((item) => item.id === tab?.childId)
  return turn && child ? { turn, child } : undefined
})
const scope = computed(() => `run-pages-${props.sessionId}`)
function select(key: string) {
  if (selected.value && childPage.value) positions.set(selected.value, childPage.value.scrollTop)
  selected.value = key
  void nextTick(() => {
    if (childPage.value) childPage.value.scrollTop = positions.get(key) || 0
  })
}
async function focusSelected() {
  await nextTick()
  const button = navigation.value?.querySelector<HTMLButtonElement>('[aria-selected="true"]')
  button?.focus({ preventScroll: true })
  button?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
}
function open(turnId: string, childId: string) {
  const child = props.turns
    .find((turn) => turn.id === turnId)
    ?.subagents?.find((item) => item.id === childId)
  if (!child) return
  const key = `${turnId}:${childId}`
  if (!tabs.value.some((tab) => tab.key === key))
    tabs.value.push({ key, turnId, childId, label: child.label || child.task || t('子 Agent') })
  select(key)
  void focusSelected()
}
function close(key: string) {
  // Closing a view never calls cancellation or conversation deletion APIs.
  if (selected.value === key) select('')
  tabs.value = tabs.value.filter((tab) => tab.key !== key)
  positions.delete(key)
  void focusSelected()
}
function navigate(event: KeyboardEvent) {
  if (!(event.target instanceof HTMLElement) || event.target.getAttribute('role') !== 'tab') return
  if (event.key === 'Delete' && selected.value) {
    event.preventDefault()
    event.stopPropagation()
    close(selected.value)
    return
  }
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  event.stopPropagation()
  const keys = ['', ...tabs.value.map((tab) => tab.key)]
  const offset = event.key === 'ArrowLeft' ? -1 : 1
  const index =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? keys.length - 1
        : (keys.indexOf(selected.value) + offset + keys.length) % keys.length
  select(keys[index])
  void focusSelected()
}
watch(
  () => props.sessionId,
  () => {
    tabs.value = []
    selected.value = ''
    positions.clear()
  },
)
defineExpose({ open })
</script>
<template>
  <div class="run-workspace">
    <nav
      ref="navigation"
      class="run-view-tabs"
      role="tablist"
      :aria-label="$t('运行与子 Agent 对话')"
      @keydown="navigate"
    >
      <button
        :id="`${scope}-list-tab`"
        type="button"
        role="tab"
        :aria-selected="!selected"
        :aria-controls="`${scope}-list`"
        :tabindex="selected ? -1 : 0"
        @click="select('')"
      >
        {{ $t('运行记录') }}
      </button>
      <div
        v-for="tab in tabs"
        :key="tab.key"
        class="child-tab"
        :class="{ selected: selected === tab.key }"
      >
        <button
          :id="`${scope}-${tab.key}-tab`"
          type="button"
          role="tab"
          :aria-controls="`${scope}-${tab.key}`"
          :aria-selected="selected === tab.key"
          :tabindex="selected === tab.key ? 0 : -1"
          :title="tab.label"
          @click="select(tab.key)"
        >
          <IconSymbol
            name="subagents"
            :size="13"
          />
          <span>{{ tab.label }}</span>
        </button>
        <button
          type="button"
          class="close-tab"
          :aria-label="$t('关闭子 Agent 页签：{0}', [tab.label])"
          @click="close(tab.key)"
        >
          <IconSymbol
            name="close"
            :size="12"
          />
        </button>
      </div>
    </nav>
    <div
      v-show="!selected"
      :id="`${scope}-list`"
      class="run-page"
      role="tabpanel"
      :aria-labelledby="`${scope}-list-tab`"
      tabindex="0"
    >
      <slot />
    </div>
    <div
      v-if="selected"
      :id="`${scope}-${selected}`"
      ref="childPage"
      class="run-page"
      role="tabpanel"
      :aria-labelledby="`${scope}-${selected}-tab`"
      tabindex="0"
    >
      <SubagentConversation
        v-if="current"
        :key="selected"
        :turn="current.turn"
        :child="current.child"
      />
      <p
        v-else
        class="empty-copy"
      >
        {{ $t('这次运行的记录已不可用。') }}
      </p>
    </div>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.run-workspace {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.run-view-tabs {
  display: flex;
  flex-shrink: 0;
  overflow-x: auto;
  gap: var(--space-1);
  border-bottom: var(--hairline) solid var(--rule);
  padding: var(--space-2) var(--space-3) 0;
}
.run-view-tabs button {
  @include mixins.interactive-row;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  padding: var(--space-3);
  border: 0;
  border-bottom: var(--focus-width) solid var(--transparent);
  background: var(--transparent);
  color: var(--ink-2);
  font-size: var(--text-xs);
  white-space: nowrap;
}
.child-tab {
  display: flex;
  align-items: center;
  min-width: 0;
  flex-shrink: 0;
}
.child-tab [role='tab'] {
  min-width: 0;
}
.child-tab span {
  @include mixins.ellipsis;
  max-width: var(--receipt-width);
}
.run-view-tabs [aria-selected='true'] {
  @include mixins.text-ink;
  border-bottom-color: var(--accent);
}
.selected {
  background: var(--accent-selected);
}
.run-view-tabs .close-tab {
  padding: var(--space-2);
}
.run-page {
  flex: 1;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  padding: var(--space-5);
}
</style>
