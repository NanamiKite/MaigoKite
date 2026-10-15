<script setup lang="ts">
import { t } from '@/i18n'
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { groupModelProfiles, type ModelOption } from '@/config/model-groups'
import { useDialog } from '@/composables/useDialog'
import IconSymbol from '@/components/common/IconSymbol.vue'
import ModelPickerGroup from './ModelPickerGroup.vue'
import { layoutPixels } from '@/utils/layout-media'
import { menuSelectionIndex } from '@/utils/composer-presentation'

const props = withDefaults(
  defineProps<{
    modelValue: string
    models: ModelOption[]
    disabled?: boolean
  }>(),
  { disabled: false },
)
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()
// getRandomValues also works when the personal web UI is served over LAN HTTP.
const id = `model-picker-${Array.from(crypto.getRandomValues(new Uint32Array(2)), (value) => value.toString(16)).join('-')}`
const trigger = ref<HTMLButtonElement | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const open = ref(false),
  query = ref(''),
  activeId = ref('')
const position = ref({ left: '0px', top: '0px' })
const keyboardNavigation = ref(false)
const collapsedGroups = ref(new Set<string>()),
  searchCollapsed = ref(new Set<string>())
const providerIds = new Map<string, string>()
function providerId(groupId: string) {
  if (!providerIds.has(groupId)) providerIds.set(groupId, `${id}-provider-${providerIds.size}`)
  return providerIds.get(groupId)!
}
function isExpanded(groupId: string) {
  return !(query.value.trim() ? searchCollapsed.value : collapsedGroups.value).has(groupId)
}
function toggleGroup(groupId: string) {
  const collapsed = query.value.trim() ? searchCollapsed.value : collapsedGroups.value
  if (collapsed.has(groupId)) collapsed.delete(groupId)
  else collapsed.add(groupId)
  activeId.value = providerId(groupId)
}
const groups = computed(() => groupModelProfiles(props.models))
const current = computed(() => props.models.find((item) => item.id === props.modelValue))
const currentGroup = computed(() =>
  groups.value.find((group) => group.models.some((item) => item.id === props.modelValue)),
)
const label = computed(
  () => current.value?.model || (props.models.length ? t('选择模型') : t('未配置模型')),
)
const visibleGroups = computed(() => {
  const terms = query.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  return groups.value
    .map((group) => ({
      ...group,
      models: group.models.filter((item) =>
        terms.every((term) =>
          `${group.name} ${group.providerName} ${item.model}`.toLocaleLowerCase().includes(term),
        ),
      ),
    }))
    .filter((group) => group.models.length)
})
const choices = computed(() =>
  visibleGroups.value.filter((group) => isExpanded(group.id)).flatMap((group) => group.models),
)
interface NavigationNode {
  id: string
  domId: string
  groupId: string
  modelId?: string
}
const navigation = computed<NavigationNode[]>(() =>
  visibleGroups.value.flatMap((group) => [
    { id: providerId(group.id), domId: providerId(group.id), groupId: group.id },
    ...(isExpanded(group.id)
      ? group.models.map((item) => ({
          id: item.id,
          domId: `${id}-${item.id}`,
          groupId: group.id,
          modelId: item.id,
        }))
      : []),
  ]),
)
const activeNode = computed(() => navigation.value.find((node) => node.id === activeId.value))
useDialog(dialog, () => open.value)
function close() {
  open.value = false
}
function place() {
  const anchor = trigger.value?.getBoundingClientRect(),
    panel = dialog.value
  if (!anchor || !panel) return
  const gap = layoutPixels('--space-3')
  position.value = {
    left: `${Math.max(gap, Math.min(anchor.right - panel.offsetWidth, window.innerWidth - panel.offsetWidth - gap))}px`,
    top: `${Math.max(layoutPixels('--titlebar-height'), anchor.top - panel.offsetHeight - gap)}px`,
  }
}
async function show(event?: MouseEvent) {
  if (props.disabled || !props.models.length) return
  keyboardNavigation.value = event?.detail === 0
  query.value = ''
  searchCollapsed.value.clear()
  activeId.value = current.value?.id || props.models[0].id
  const selectedGroup = groups.value.find((group) =>
    group.models.some((item) => item.id === activeId.value),
  )
  if (selectedGroup) collapsedGroups.value.delete(selectedGroup.id)
  open.value = true
  await nextTick()
  place()
  search.value?.focus()
  scrollToActive()
}
function scrollToActive() {
  const node = activeNode.value
  const element =
    node && document.getElementById(node.modelId ? node.domId : `${node.domId}-heading`)
  if (element && dialog.value?.contains(element)) element.scrollIntoView({ block: 'nearest' })
}
function choose(value: string) {
  if (props.disabled || !props.models.some((item) => item.id === value)) return
  if (value !== props.modelValue) emit('update:modelValue', value)
  close()
}
function onKey(event: KeyboardEvent) {
  if (event.isComposing || event.ctrlKey || event.metaKey) return
  if (event.altKey && /^[1-9]$/.test(event.key)) {
    event.preventDefault()
    const choice = choices.value[Number(event.key) - 1]
    if (choice) choose(choice.id)
    return
  }
  if (event.altKey) return
  keyboardNavigation.value = true
  const focusedId =
    event.target instanceof HTMLElement
      ? event.target.closest<HTMLElement>('[data-node-id]')?.dataset.nodeId
      : undefined
  const node = navigation.value.find((item) => item.id === (focusedId || activeId.value))
  // Preserve caret movement in the search text; tree arrows apply when it is empty.
  if (!query.value && node && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
    event.preventDefault()
    if (event.key === 'ArrowLeft') {
      if (node.modelId) activeId.value = providerId(node.groupId)
      else if (isExpanded(node.groupId)) toggleGroup(node.groupId)
    } else if (!node.modelId) {
      if (!isExpanded(node.groupId)) toggleGroup(node.groupId)
      else
        activeId.value =
          visibleGroups.value.find((group) => group.id === node.groupId)?.models[0]?.id || node.id
    }
    search.value?.focus()
    return
  }
  const index = menuSelectionIndex(
    event.key,
    navigation.value.findIndex((item) => item.id === (focusedId || activeId.value)),
    navigation.value.length,
  )
  if (index !== null) {
    event.preventDefault()
    activeId.value = navigation.value[index]?.id || ''
    search.value?.focus()
  } else if (event.key === 'Enter' && event.target === search.value) {
    event.preventDefault()
    if (activeNode.value?.modelId) choose(activeNode.value.modelId)
    else if (activeNode.value) toggleGroup(activeNode.value.groupId)
  }
}
watch(query, () => {
  searchCollapsed.value.clear()
  activeId.value =
    choices.value.find((item) => !query.value.trim() && item.id === props.modelValue)?.id ||
    choices.value[0]?.id ||
    navigation.value[0]?.id ||
    ''
})
watch(navigation, () => {
  if (!navigation.value.some((item) => item.id === activeId.value))
    activeId.value = choices.value[0]?.id || navigation.value[0]?.id || ''
})
watch(activeId, async () => {
  await nextTick()
  if (keyboardNavigation.value && document.activeElement === search.value) scrollToActive()
})
watch(navigation, async () => {
  if (open.value) {
    await nextTick()
    place()
  }
})
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) close()
  },
)
watch(() => props.modelValue, close)
watch(open, (visible) => {
  if (visible) {
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
  } else {
    window.removeEventListener('resize', place)
    window.removeEventListener('scroll', place, true)
  }
})
onScopeDispose(() => {
  window.removeEventListener('resize', place)
  window.removeEventListener('scroll', place, true)
})
</script>
<template>
  <button
    ref="trigger"
    type="button"
    class="model-trigger"
    :disabled="disabled || !models.length"
    :title="current ? `${currentGroup?.name || current.name} · ${current.model}` : label"
    :aria-label="$t('当前对话模型：{0}', [label])"
    aria-haspopup="dialog"
    :aria-expanded="open"
    :aria-controls="open ? id : undefined"
    @click="show"
  >
    <IconSymbol
      name="cube"
      :size="14"
    />
    <span>{{ label }}</span>
    <IconSymbol
      class="model-chevron"
      name="chevron"
      :size="12"
    />
  </button>
  <Teleport to="body">
    <dialog
      :id="id"
      ref="dialog"
      class="model-menu inkline-shell"
      :style="position"
      :aria-label="$t('选择对话模型')"
      @cancel.prevent="close"
      @close="close"
      @click.self="close"
      @keydown="onKey"
      @keydown.esc.stop
      @pointermove="keyboardNavigation = false"
      @pointerdown="keyboardNavigation = false"
      @pointerleave="keyboardNavigation = false"
    >
      <label class="model-search">
        <IconSymbol
          name="search"
          :size="15"
        />
        <input
          ref="search"
          v-model="query"
          type="text"
          role="combobox"
          aria-haspopup="tree"
          :aria-label="$t('搜索模型或服务')"
          aria-autocomplete="list"
          :aria-expanded="open"
          :aria-controls="`${id}-list`"
          :aria-activedescendant="activeNode?.domId"
          :placeholder="$t('搜索模型或服务…')"
          autocomplete="off"
          @input="keyboardNavigation = true"
        />
      </label>
      <div
        :id="`${id}-list`"
        class="model-list"
        role="tree"
        :aria-label="$t('已配置的模型')"
      >
        <ModelPickerGroup
          v-for="group in visibleGroups"
          :key="group.id"
          :group="group"
          :menu-id="id"
          :provider-id="providerId(group.id)"
          :expanded="isExpanded(group.id)"
          :model-value="modelValue"
          :active-id="activeId"
          :keyboard-navigation="keyboardNavigation"
          @toggle="toggleGroup(group.id)"
          @highlight="activeId = $event"
          @choose="choose"
        />
      </div>
      <p
        v-if="!visibleGroups.length"
        class="model-empty"
        role="status"
      >
        {{ $t('没有匹配的模型。') }}
      </p>
      <footer>
        <router-link
          to="/settings?tab=models"
          @click="close"
        >
          <IconSymbol
            name="settings"
            :size="14"
          />
          {{ $t('管理模型') }}
        </router-link>
        <span>{{ $t('↑ ↓ 选择 · Enter 确认 · Alt + 数字') }}</span>
      </footer>
    </dialog>
  </Teleport>
</template>

<style scoped lang="scss">
@use '@/styles/model-picker';
</style>
