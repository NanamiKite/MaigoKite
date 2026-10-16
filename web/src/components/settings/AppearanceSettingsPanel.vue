<script setup lang="ts">
import { ref, watch } from 'vue'
import { useUIStore, accents, type Accent, type ThemeMode, type TextSize } from '@/stores/ui'
import { useSettingsStore } from '@/stores/settings'
import { desktopEffects } from '@/composables/useElectron'
import { t, type Language } from '@/i18n'
import DeskBackgroundSettings from './DeskBackgroundSettings.vue'
import EnterBehaviorField from './EnterBehaviorField.vue'
import InkSelect from '@/components/common/InkSelect.vue'
const ui = useUIStore(),
  settings = useSettingsStore()
const name = ref(settings.profile.assistantName),
  shortcut = ref(ui.appearance.capsuleShortcut)
const nameResult = ref(''),
  nameError = ref(''),
  shortcutResult = ref(''),
  shortcutError = ref('')
const savingShortcut = ref(false)
watch(
  () => settings.profile.assistantName,
  (value) => {
    name.value = value
  },
)
watch(name, () => {
  nameResult.value = ''
  nameError.value = ''
})
const swatches: Record<Accent, string> = {
  stone: '--stone',
  blue: '--accent-blue',
  green: '--accent-green',
  earth: '--accent-earth',
  lotus: '--accent-lotus',
  ink: '--accent-black',
}
async function saveName() {
  if (!name.value.trim() || settings.saving) return
  nameResult.value = ''
  nameError.value = ''
  if (await settings.savePreferences(name.value.trim())) nameResult.value = t('已保存')
  else nameError.value = settings.error || t('保存失败')
}
async function saveShortcut() {
  if (savingShortcut.value) return
  savingShortcut.value = true
  shortcutResult.value = ''
  shortcutError.value = ''
  try {
    await ui.setCapsuleShortcut(shortcut.value)
    shortcutResult.value = t('已保存')
  } catch (reason) {
    shortcutError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    savingShortcut.value = false
  }
}
</script>
<template>
  <section class="settings-panel appearance-settings">
    <header class="panel-heading">
      <h2>{{ $t('外观与称呼') }}</h2>
    </header>
    <section class="setting-group">
      <h3>{{ $t('称呼与语言') }}</h3>
      <label
        class="setting-row"
        data-settings-field="language"
      >
        <span>{{ $t('语言') }}</span>
        <InkSelect
          :value="ui.appearance.language || 'zh-CN'"
          :options="[
            { value: 'zh-CN', label: $t('简体中文') },
            { value: 'en', label: 'English' },
            { value: 'system', label: $t('跟随系统') },
          ]"
          @change="
            ui.setAppearance({ language: ($event.target as HTMLSelectElement).value as Language })
          "
        />
      </label>
      <div
        class="setting-row"
        data-settings-field="assistant-name"
      >
        <label for="assistant-name">{{ $t('助手名字') }}</label>
        <div>
          <div class="setting-control">
            <input
              id="assistant-name"
              v-model="name"
              maxlength="32"
            />
            <button
              class="secondary-button"
              :disabled="settings.saving || !name.trim()"
              :aria-busy="settings.saving || undefined"
              @click="saveName"
            >
              {{ $t('保存') }}
            </button>
          </div>
          <p
            v-if="nameError"
            class="field-error"
            role="alert"
          >
            {{ nameError }}
            <button
              class="text-button"
              :disabled="settings.saving"
              @click="saveName"
            >
              {{ $t('重试') }}
            </button>
          </p>
          <p
            v-else-if="nameResult"
            class="field-saved"
            role="status"
          >
            {{ nameResult }}
          </p>
        </div>
      </div>
      <label
        class="setting-row"
        data-settings-field="greeting-name"
      >
        <span>{{ $t('怎么称呼你') }}</span>
        <input
          :value="ui.appearance.greetingName"
          maxlength="32"
          @input="ui.setAppearance({ greetingName: ($event.target as HTMLInputElement).value })"
        />
      </label>
      <label
        class="setting-row"
        data-settings-field="avatar"
      >
        <span>{{ $t('头像字') }}</span>
        <input
          class="avatar-input"
          :value="ui.appearance.avatar"
          @input="
            ui.setAppearance({
              avatar: Array.from(($event.target as HTMLInputElement).value)[0] || '助',
            })
          "
        />
      </label>
    </section>
    <section class="setting-group">
      <h3>{{ $t('纸面与文字') }}</h3>
      <label
        class="setting-row"
        data-settings-field="theme"
      >
        <span>{{ $t('纸页') }}</span>
        <InkSelect
          :value="ui.appearance.theme"
          :options="[
            { value: 'system', label: $t('跟随系统') },
            { value: 'light', label: $t('宣纸') },
            { value: 'dark', label: $t('松烟') },
          ]"
          @change="
            ui.setAppearance({ theme: ($event.target as HTMLSelectElement).value as ThemeMode })
          "
        />
      </label>
      <div
        class="setting-row"
        data-settings-field="accent"
      >
        <span>{{ $t('强调色') }}</span>
        <div class="accent-presets">
          <button
            v-for="accent in accents"
            :key="accent.id"
            :aria-label="$t(accent.label)"
            :aria-pressed="ui.appearance.accent === accent.id"
            :style="{ '--swatch': 'var(' + swatches[accent.id] + ')' }"
            @click="ui.setAppearance({ accent: accent.id })"
          >
            <i />
            <span>{{ $t(accent.label) }}</span>
          </button>
        </div>
      </div>
      <label
        class="setting-row"
        data-settings-field="text-size"
      >
        <span>{{ $t('字号') }}</span>
        <InkSelect
          :value="ui.appearance.textSize || 'default'"
          :options="[
            { value: 'small', label: $t('紧凑') },
            { value: 'default', label: $t('标准') },
            { value: 'large', label: $t('舒展') },
          ]"
          @change="
            ui.setAppearance({ textSize: ($event.target as HTMLSelectElement).value as TextSize })
          "
        />
      </label>
      <label
        class="setting-row"
        data-settings-field="texture"
      >
        <span>{{ $t('纸纹') }}</span>
        <input
          type="checkbox"
          :checked="ui.appearance.texture"
          @change="ui.setAppearance({ texture: ($event.target as HTMLInputElement).checked })"
        />
      </label>
    </section>
    <DeskBackgroundSettings />
    <section class="setting-group">
      <h3>{{ $t('输入与状态') }}</h3>
      <EnterBehaviorField />
      <label
        class="setting-row"
        data-settings-field="context-usage"
      >
        <span>{{ $t('显示模型上下文窗口用量') }}</span>
        <input
          type="checkbox"
          :checked="ui.appearance.showContextUsage !== false"
          @change="
            ui.setAppearance({ showContextUsage: ($event.target as HTMLInputElement).checked })
          "
        />
      </label>
      <div
        v-if="desktopEffects"
        class="setting-row"
        data-settings-field="capsule-shortcut"
      >
        <label for="capsule-shortcut">{{ $t('速记快捷键') }}</label>
        <div>
          <div class="setting-control">
            <input
              id="capsule-shortcut"
              v-model="shortcut"
              maxlength="80"
              placeholder="Alt+Space"
            />
            <button
              class="secondary-button"
              :disabled="savingShortcut || !shortcut.trim()"
              @click="saveShortcut"
            >
              {{ $t('设置') }}
            </button>
          </div>
          <p
            v-if="shortcutError"
            class="field-error"
            role="alert"
          >
            {{ shortcutError }}
          </p>
          <p
            v-else-if="shortcutResult"
            class="field-saved"
            role="status"
          >
            {{ shortcutResult }}
          </p>
        </div>
      </div>
      <p
        v-else
        class="panel-copy"
      >
        {{ $t('速记快捷键在桌面应用中设置。') }}
      </p>
    </section>
  </section>
</template>
<style scoped lang="scss"></style>
