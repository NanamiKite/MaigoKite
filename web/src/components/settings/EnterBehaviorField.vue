<script setup lang="ts">
import { useUIStore } from '@/stores/ui'
import InkSelect from '@/components/common/InkSelect.vue'
const ui = useUIStore()
function change(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'send' || value === 'newline') ui.setAppearance({ enterBehavior: value })
}
</script>
<template>
  <div data-settings-field="enter-behavior">
    <label class="setting-row">
      <span>{{ $t('发送快捷键') }}</span>
      <InkSelect
        :value="ui.appearance.enterBehavior || 'newline'"
        :options="[
          { value: 'newline', label: $t('Alt+S 发送') },
          { value: 'send', label: $t('Enter 发送') },
        ]"
        @change="change"
      />
    </label>
    <p class="panel-copy">
      {{
        ui.appearance.enterBehavior === 'send'
          ? $t('Shift+Enter 换行，Alt+S 也可发送。')
          : $t('Enter 换行，Alt+S 发送。')
      }}
    </p>
    <label
      v-if="ui.appearance.enterBehavior === 'send'"
      class="setting-row"
    >
      <span>{{ $t('Enter 发送前二次确认') }}</span>
      <input
        type="checkbox"
        :checked="ui.appearance.confirmEnterSend === true"
        @change="
          ui.setAppearance({ confirmEnterSend: ($event.target as HTMLInputElement).checked })
        "
      />
    </label>
  </div>
</template>
