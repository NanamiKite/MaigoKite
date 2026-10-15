<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useDialog } from '@/composables/useDialog'
import { confirmation, settleConfirmation } from '@/utils/confirm-action'
import IconSymbol from './IconSymbol.vue'
const dialog = ref<HTMLDialogElement | null>(null)
useDialog(dialog, () => confirmation.value !== null)
function closed() { if (!dialog.value?.open) settleConfirmation(false) }
onBeforeUnmount(() => { settleConfirmation(false) })
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog" class="ink-confirm inkline-shell" aria-labelledby="ink-confirm-title" aria-describedby="ink-confirm-message"
      @cancel.prevent="settleConfirmation(false)" @close="closed" @keydown.stop
    >
      <template v-if="confirmation">
        <header>
          <IconSymbol :name="confirmation.danger ? 'alert' : 'info'" :size="20" :class="{ danger: confirmation.danger }" />
          <h2 id="ink-confirm-title">{{ confirmation.title || (confirmation.danger ? $t('确认操作') : $t('有未保存的修改')) }}</h2>
        </header>
        <p id="ink-confirm-message">{{ confirmation.message }}</p>
        <footer>
          <button type="button" class="secondary-button" autofocus @click="settleConfirmation(false)">{{ $t('取消') }}</button>
          <button type="button" :class="confirmation.danger ? 'danger-button' : 'primary-button'" @click="settleConfirmation(true)">{{ confirmation.confirmLabel || $t('确认') }}</button>
        </footer>
      </template>
    </dialog>
  </Teleport>
</template>
