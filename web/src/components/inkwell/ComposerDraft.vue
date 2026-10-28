<script setup lang="ts">
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import { t } from '@/i18n'
import { menuSelectionIndex } from '@/utils/composer-presentation'
import MentionMenu, { type Mention } from './MentionMenu.vue'
import SlashMenu, { type SlashCommand } from './SlashMenu.vue'
const props = defineProps<{
  modelValue: string
  menuId: string
  mentions: Mention[]
  mentionQuery?: string
  loading?: boolean
  commands: SlashCommand[]
  commandsLoading?: boolean
  commandsError?: string
  placeholder: string
  disabled: boolean
}>()
const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'keydown', value: KeyboardEvent): void
  (event: 'mention', value: Mention): void
  (event: 'command', value: SlashCommand): void
  (event: 'focus'): void
  (event: 'commands-open', value: boolean): void
  (event: 'retry-commands'): void
}>()
const textarea = ref<HTMLTextAreaElement | null>(null)
const selected = ref(0),
  dismissed = ref(false)
const launched = ref(false)
const menuQuery = ref('')
const slashQuery = computed(() => props.modelValue.match(/^\/([^\s]*)$/)?.[1])
const commandMenuOpen = computed(
  () => !dismissed.value && (launched.value || slashQuery.value !== undefined),
)
const commands = computed(() => {
  if (!commandMenuOpen.value) return []
  const query = (launched.value ? menuQuery.value : slashQuery.value || '').toLocaleLowerCase()
  return props.commands.filter((item) => {
    const label = item.group === '技能' ? item.label : t(item.label)
    return `${item.id} ${label} ${item.keywords || ''}`.toLocaleLowerCase().includes(query)
  })
})
const referencing = computed(() => !launched.value && props.mentionQuery !== undefined)
const count = computed(() => (referencing.value ? props.mentions.length : commands.value.length))
function menu() {
  return !dismissed.value && count.value
}
function listId() {
  return props.menuId + (referencing.value ? '-mentions' : '-commands')
}
function chooseCommand(item: SlashCommand) {
  if (item.disabled) return
  // Typed commands follow the parent's draft; only the + menu owns its open state.
  if (launched.value) closeMenu()
  else if (slashQuery.value !== undefined) emit('update:modelValue', '')
  textarea.value?.focus({ preventScroll: true })
  emit('command', item)
}
function select(index: number) {
  const mention = referencing.value ? props.mentions[index] : undefined
  if (mention) emit('mention', mention)
  else if (!referencing.value && commands.value[index]) chooseCommand(commands.value[index])
}
function closeMenu() {
  const open = !dismissed.value && (referencing.value || commandMenuOpen.value)
  dismissed.value = true
  launched.value = false
  return open
}
function dismissOutside(event: PointerEvent) {
  if (event.target === textarea.value) return
  if (
    event.target instanceof Element &&
    event.target.closest('.slash-menu, .mention-menu, .add-button')
  )
    return
  closeMenu()
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if ((event.key === 'Escape' || (event.key === 'Tab' && event.shiftKey)) && closeMenu()) {
    event.preventDefault()
    event.stopPropagation()
    textarea.value?.focus({ preventScroll: true })
    return
  }
  if (event.target instanceof HTMLButtonElement) return
  const index =
    menu() && !event.shiftKey && !event.ctrlKey && !event.metaKey
      ? menuSelectionIndex(event.key, selected.value, count.value)
      : null
  if (index !== null) {
    event.preventDefault()
    selected.value = index
    return
  }
  if (menu() && event.altKey && /^[1-9]$/.test(event.key)) {
    event.preventDefault()
    select(Number(event.key) - 1)
    return
  }
  if (
    menu() &&
    !event.shiftKey &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    ['Enter', 'Tab'].includes(event.key)
  ) {
    event.preventDefault()
    select(selected.value)
    return
  }
  // Search keystrokes must never submit or replace the actual message draft.
  if (!launched.value) emit('keydown', event)
}
watch(
  () => props.modelValue,
  () => {
    selected.value = 0
    dismissed.value = false
    launched.value = false
  },
)
watch(menuQuery, () => {
  selected.value = 0
})
watch(count, (length) => {
  selected.value = Math.min(selected.value, Math.max(0, length - 1))
})
watch(commandMenuOpen, (open) => emit('commands-open', open), { immediate: true })
watch(() => props.menuId, closeMenu)
onMounted(() => document.addEventListener('pointerdown', dismissOutside))
onScopeDispose(() => document.removeEventListener('pointerdown', dismissOutside))
watch(
  () => props.modelValue,
  async () => {
    await nextTick()
    if (!textarea.value) return
    textarea.value.style.height = 'auto'
    const height = Number.parseFloat(getComputedStyle(textarea.value).lineHeight)
    textarea.value.style.height =
      Math.min(
        textarea.value.scrollHeight,
        (Number.isFinite(height) ? height : textarea.value.scrollHeight) * 10,
      ) + 'px'
  },
  { flush: 'post', immediate: true },
)
defineExpose({
  focus() {
    textarea.value?.focus()
  },
  closeMenu,
  toggleCommands() {
    const wasOpen = commandMenuOpen.value
    closeMenu()
    textarea.value?.focus()
    if (!wasOpen) {
      launched.value = true
      menuQuery.value = ''
      dismissed.value = false
      selected.value = 0
    }
  },
})
</script>
<template>
  <MentionMenu
    v-if="!dismissed && referencing"
    :id="menuId + '-mentions'"
    :items="mentions"
    :selected="selected"
    :loading="loading"
    @select="emit('mention', $event)"
  />
  <SlashMenu
    v-else-if="commandMenuOpen"
    :id="menuId + '-commands'"
    :items="commands"
    :selected="selected"
    :searchable="launched"
    :query="menuQuery"
    :loading="commandsLoading"
    :error="commandsError"
    @update:query="menuQuery = $event"
    @highlight="selected = $event"
    @retry="emit('retry-commands')"
    @keydown="keydown"
    @select="chooseCommand"
  />
  <textarea
    ref="textarea"
    :value="modelValue"
    :role="menu() ? 'combobox' : undefined"
    :aria-expanded="menu() ? true : undefined"
    :aria-controls="menu() ? listId() : undefined"
    :aria-activedescendant="menu() ? listId() + '-option-' + selected : undefined"
    :placeholder="placeholder"
    :disabled="disabled"
    rows="1"
    :aria-label="$t('消息内容')"
    @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    @keydown="keydown"
    @focus="emit('focus')"
  />
</template>
<style scoped lang="scss">
textarea {
  display: block;
  width: 100%;
  max-height: calc(var(--size-26) * 10);
  min-height: var(--size-26);
  border: 0;
  outline: 0;
  padding: 0 0 var(--space-4);
  resize: none;
  color: var(--ink);
  font-size: var(--type-body);
  line-height: var(--leading-message);
  background: var(--transparent);
}
textarea::placeholder {
  color: var(--ink-3);
}
</style>
