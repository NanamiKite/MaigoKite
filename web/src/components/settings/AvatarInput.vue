<script setup lang="ts">
import { defaultAvatar, useUIStore } from '@/stores/ui'
const ui = useUIStore()
function firstCharacter(value: string | null | undefined) {
  return Array.from(value ?? '').find((character) => !/^[\s\p{C}]$/u.test(character))
}
function commit(event: InputEvent | CompositionEvent) {
  if (!(event.target instanceof HTMLInputElement)) return
  if (event instanceof CompositionEvent && !event.data) {
    event.target.value = ui.appearance.avatar
    return
  }
  const next = firstCharacter(event.data) ?? firstCharacter(event.target.value) ?? defaultAvatar
  if (next !== ui.appearance.avatar) ui.setAppearance({ avatar: next })
  event.target.value = ui.appearance.avatar
}
function commitInput(event: InputEvent) {
  if (!event.isComposing) commit(event)
}
</script>

<template>
  <input
    :value="ui.appearance.avatar"
    @input="commitInput"
    @compositionend="commit"
  />
</template>
