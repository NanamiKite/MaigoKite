<script setup lang="ts">
import { computed, ref } from 'vue'
import { t } from '@/i18n'
import { useDeskStore } from '@/stores/desk'
import { readDeskPalette } from '@/utils/desk-palette'
import AppearancePreview from './AppearancePreview.vue'
import DeskFocalPreview from './DeskFocalPreview.vue'
import InkLoader from '@/components/common/InkLoader.vue'
const desk = useDeskStore(),
  picker = ref<HTMLInputElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const valueText = computed(() =>
  t('{0}% · 正文至少 7:1，次要文字至少 4.5:1', [Math.round(desk.preferences.visibility * 100)]),
)
async function choose(event: Event) {
  const input = event.target as HTMLInputElement,
    file = input.files?.[0]
  input.value = ''
  const root = panel.value?.closest<HTMLElement>('.inkline-shell')
  if (!file || !root) return
  const blur = Number.parseFloat(getComputedStyle(root).getPropertyValue('--desk-blur-radius'))
  await desk.importImage(file, readDeskPalette(root), blur)
}
</script>
<template>
  <section
    ref="panel"
    class="setting-group desk-settings"
    data-settings-field="desk-background"
  >
    <header class="desk-settings-heading">
      <h3>{{ $t('案头背景') }}</h3>
      <label class="desk-switch">
        <span>{{ $t('启用背景') }}</span>
        <input
          type="checkbox"
          :checked="desk.preferences.on"
          :disabled="!desk.image || desk.busy"
          @change="desk.change({ on: ($event.target as HTMLInputElement).checked })"
        />
      </label>
    </header>
    <p class="panel-copy">
      {{ $t('图片留在案头，文字落在纸上。') }}
    </p>
    <div class="desk-actions">
      <input
        ref="picker"
        class="desk-file"
        type="file"
        accept="image/png,image/jpeg,image/webp,image/avif"
        :aria-label="$t('选择背景图片')"
        @change="choose"
      />
      <button
        type="button"
        class="secondary-button"
        :disabled="desk.busy"
        @click="picker?.click()"
      >
        {{ $t(desk.image ? '更换图片' : '选择图片') }}
      </button>
      <button
        v-if="desk.image"
        type="button"
        class="text-button"
        :disabled="desk.busy"
        @click="desk.remove"
      >
        {{ $t('移除图片') }}
      </button>
    </div>
    <InkLoader v-if="desk.busy" />
    <p
      v-if="desk.notice"
      class="field-error"
      role="alert"
    >
      {{ $t(desk.notice) }}
    </p>
    <template v-if="desk.image">
      <DeskFocalPreview />
      <label class="setting-row">
        <span>{{ $t('显现度') }}</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          :value="desk.preferences.visibility"
          :aria-valuetext="valueText"
          @input="desk.change({ visibility: Number(($event.target as HTMLInputElement).value) })"
        />
      </label>
      <p
        class="desk-readability"
        role="status"
      >
        {{ valueText }}
      </p>
    </template>
    <div
      class="desk-preview"
      :class="{ 'desk-preview-on': desk.active }"
    >
      <AppearancePreview />
    </div>
  </section>
</template>
<style scoped lang="scss">
.desk-settings-heading,
.desk-switch,
.desk-actions {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}
.desk-settings-heading {
  justify-content: space-between;
  flex-wrap: wrap;
}
.desk-settings-heading h3 {
  margin: 0;
}
.desk-switch {
  font-size: var(--text-sm);
  color: var(--ink-2);
}
.desk-actions {
  margin-block: var(--space-5);
}
.desk-file {
  display: none;
}
.desk-settings input[type='range'] {
  accent-color: var(--accent);
  padding: 0;
  cursor: pointer;
}
.desk-readability {
  font-size: var(--text-xs);
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
.desk-preview {
  margin-top: var(--space-5);
  padding: var(--space-5);
  border-radius: var(--radius-sm);
  background: var(--paper-3);
}
.desk-preview-on {
  background-image: var(--desk-clear);
  background-size: cover;
  background-position: var(--desk-position);
}
</style>
