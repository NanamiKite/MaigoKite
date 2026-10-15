<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch, type CSSProperties } from 'vue'
import { useDialog } from '@/composables/useDialog'
import { layoutPixels } from '@/utils/layout-media'
import { menuSelectionIndex } from '@/utils/composer-presentation'
import IconSymbol from './IconSymbol.vue'

defineOptions({ inheritAttrs: false })
const props = defineProps<{
  modelValue?: string
  value?: string
  options: readonly { value: string; label: string; disabled?: boolean }[]
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; change: [event: Event] }>()
const id = `ink-select-${Array.from(crypto.getRandomValues(new Uint32Array(2)), (value) => value.toString(16)).join('-')}`
const trigger = ref<HTMLButtonElement | null>(null),
  native = ref<HTMLSelectElement | null>(null)
const dialog = ref<HTMLDialogElement | null>(null),
  list = ref<HTMLElement | null>(null)
const open = ref(false),
  active = ref('')
const keyboardNavigation = ref(false)
const position = ref<CSSProperties>({})
const selected = computed(() => props.modelValue ?? props.value ?? '')
const current = computed(() => props.options.find((option) => option.value === selected.value))
const enabled = computed(() => props.options.filter((option) => !option.disabled))
useDialog(dialog, () => open.value)
let letters = '',
  typingTimer: ReturnType<typeof setTimeout> | undefined
function close() {
  open.value = false
  letters = ''
  if (typingTimer) clearTimeout(typingTimer)
}
function closed() {
  if (!dialog.value?.open) close()
}
function place() {
  const anchor = trigger.value?.getBoundingClientRect(),
    panel = dialog.value
  if (!anchor || !panel) return
  const gap = layoutPixels('--space-2'),
    margin = layoutPixels('--space-3')
  const width = Math.min(
    Math.max(anchor.width, layoutPixels('--select-menu-min-width')),
    window.innerWidth - margin * 2,
  )
  const below = window.innerHeight - anchor.bottom - gap - margin,
    above = anchor.top - gap - margin
  const desired = Math.min(panel.scrollHeight, layoutPixels('--select-menu-height'))
  const upward = below < desired && above > below
  const height = Math.max(0, Math.min(desired, upward ? above : below))
  position.value = {
    width: `${width}px`,
    maxHeight: `${height}px`,
    left: `${Math.max(margin, Math.min(anchor.left, window.innerWidth - width - margin))}px`,
    top: `${upward ? anchor.top - gap - height : anchor.bottom + gap}px`,
  }
}
function follow() {
  list.value
    ?.querySelector<HTMLElement>('[data-active="true"]')
    ?.scrollIntoView({ block: 'nearest' })
}
async function show(last = false, keyboard = false) {
  if (props.disabled || trigger.value?.matches(':disabled') || !enabled.value.length) return
  keyboardNavigation.value = keyboard
  active.value =
    enabled.value.find((option) => option.value === selected.value)?.value ??
    enabled.value[last ? enabled.value.length - 1 : 0].value
  open.value = true
  await nextTick()
  place()
  list.value?.focus({ preventScroll: true })
  follow()
}
function choose(value: string) {
  if (
    props.disabled ||
    trigger.value?.matches(':disabled') ||
    !enabled.value.some((option) => option.value === value)
  )
    return
  close()
  if (value === selected.value) return
  emit('update:modelValue', value)
  // Keep existing HTMLSelectElement change handlers and form values unchanged.
  if (native.value) {
    native.value.value = value
    native.value.dispatchEvent(new Event('change'))
  }
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return
  keyboardNavigation.value = true
  const index = menuSelectionIndex(
    event.key,
    enabled.value.findIndex((option) => option.value === active.value),
    enabled.value.length,
  )
  if (index !== null) {
    event.preventDefault()
    active.value = enabled.value[index]?.value ?? ''
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    choose(active.value)
  } else if (event.key.length === 1) {
    letters += event.key.toLocaleLowerCase()
    if (typingTimer) clearTimeout(typingTimer)
    typingTimer = setTimeout(() => {
      letters = ''
    }, layoutPixels('--select-typeahead-ms'))
    const match = enabled.value.find((option) =>
      option.label.toLocaleLowerCase().startsWith(letters),
    )
    if (match) active.value = match.value
  }
}
watch(active, () => {
  if (keyboardNavigation.value) void nextTick(follow)
})
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) close()
  },
)
watch(
  () => props.options,
  () => {
    if (!open.value) return
    if (!enabled.value.length) close()
    else if (!enabled.value.some((option) => option.value === active.value))
      active.value = enabled.value[0].value
    void nextTick(place)
  },
)
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
  if (typingTimer) clearTimeout(typingTimer)
  window.removeEventListener('resize', place)
  window.removeEventListener('scroll', place, true)
})
</script>

<template>
  <span class="ink-select">
    <button
      v-bind="$attrs"
      ref="trigger"
      class="ink-select-trigger"
      type="button"
      :disabled="disabled || !enabled.length"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-controls="open ? id : undefined"
      @click="open ? close() : show(false, $event.detail === 0)"
      @keydown.down.prevent="show(false, true)"
      @keydown.up.prevent="show(true, true)"
    >
      <span>{{ current?.label || $t('请选择') }}</span>
      <IconSymbol
        name="chevron"
        :size="14"
        class="ink-select-chevron"
      />
    </button>
    <select
      ref="native"
      hidden
      aria-hidden="true"
      tabindex="-1"
      :value="selected"
      :disabled="disabled"
      @change="emit('change', $event)"
    >
      <option
        v-for="option in options"
        :key="option.value"
        :value="option.value"
        :disabled="option.disabled"
      >
        {{ option.label }}
      </option>
    </select>
    <Teleport to="body">
      <dialog
        ref="dialog"
        class="ink-select-menu inkline-shell"
        :style="position"
        :aria-label="String($attrs['aria-label'] || current?.label || $t('请选择'))"
        :data-keyboard-navigation="keyboardNavigation"
        @cancel.prevent="close"
        @close="closed"
        @click.self="close"
        @keydown.stop="keydown"
        @pointermove="keyboardNavigation = false"
        @pointerdown="keyboardNavigation = false"
        @pointerleave="keyboardNavigation = false"
      >
        <div
          :id="id"
          ref="list"
          role="listbox"
          tabindex="0"
          :aria-label="String($attrs['aria-label'] || current?.label || $t('请选择'))"
          :aria-activedescendant="`${id}-${options.findIndex((option) => option.value === active)}`"
        >
          <button
            v-for="(option, index) in options"
            :id="`${id}-${index}`"
            :key="option.value"
            type="button"
            class="ink-select-option"
            role="option"
            tabindex="-1"
            :disabled="option.disabled"
            :aria-selected="option.value === selected"
            :data-active="!option.disabled && option.value === active"
            @pointermove="!option.disabled && (active = option.value)"
            @click="choose(option.value)"
          >
            <span>{{ option.label }}</span>
            <IconSymbol
              v-if="option.value === selected"
              name="check"
              :size="14"
            />
          </button>
        </div>
      </dialog>
    </Teleport>
  </span>
</template>
