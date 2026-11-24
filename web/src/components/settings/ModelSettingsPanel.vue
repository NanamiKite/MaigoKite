<script setup lang="ts">
import { computed, reactive, ref, toRefs, watch } from 'vue'
import { useModelSettings } from '@/composables/useModelSettings'
import { providerChoices, choiceGroups } from '@/config/model-provider-choices'
import IconSymbol from '@/components/common/IconSymbol.vue'
import ModelProfileEditor from './ModelProfileEditor.vue'
import ModelServiceCard from './ModelServiceCard.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import { t } from '@/i18n'
const editor = reactive(useModelSettings())
defineProps<{ loading?: boolean }>()
const {
  settings,
  mode,
  modelGroups,
  profileCredential,
  selectProfile,
  makeDefault,
  showProviderPicker,
  closeEditor,
  beginAdd,
} = toRefs(editor)
const providerSearch = ref('')
const collapsedGroups = ref(new Set<string>())
function setGroupExpanded(id: string, expanded: boolean) {
  if (expanded) collapsedGroups.value.delete(id)
  else collapsedGroups.value.add(id)
}
const feedback = ref(''),
  feedbackError = ref('')
function result(ok: boolean, message: string) {
  feedback.value = ok ? message : ''
  feedbackError.value = ok ? '' : message
}
async function setDefault(id: string) {
  feedback.value = feedbackError.value = ''
  await makeDefault.value(id)
  result(!settings.value.error, settings.value.error || settings.value.success || t('已保存'))
}
const filteredGroups = computed(() => {
  const query = providerSearch.value.trim().toLocaleLowerCase()
  return choiceGroups
    .map((group) => ({
      name: group,
      choices: providerChoices.filter(
        (item) =>
          item.group === group &&
          (!query ||
            `${item.name} ${item.description} ${t(item.name)} ${t(item.description)} ${item.id}`
              .toLocaleLowerCase()
              .includes(query)),
      ),
    }))
    .filter((group) => group.choices.length)
})
watch(mode, () => {
  providerSearch.value = ''
})
</script>
<template>
  <section
    class="model-settings settings-panel"
    data-settings-field="models"
  >
    <header class="panel-heading">
      <div>
        <h2>{{ $t('模型') }}</h2>
      </div>
      <button
        v-if="mode === 'list'"
        type="button"
        class="primary-button add-button"
        data-testid="add-model"
        @click="showProviderPicker"
      >
        <IconSymbol
          name="plus"
          :size="15"
        />
        {{ $t('添加服务') }}
      </button>
    </header>

    <div
      v-if="loading"
      class="profile-loading"
    >
      <InkLoader />
    </div>
    <div
      v-else-if="mode === 'list'"
      class="profile-list"
    >
      <ModelServiceCard
        v-for="group in modelGroups"
        :key="group.id"
        :group="group"
        :expanded="!collapsedGroups.has(group.id)"
        :saving="settings.saving"
        :profile-credential="profileCredential"
        @expanded="setGroupExpanded(group.id, $event)"
        @edit="selectProfile"
        @default="setDefault"
      />

      <div
        v-if="!settings.modelProfiles.length"
        class="empty-profile"
      >
        <span class="empty-icon">
          <IconSymbol
            name="brain"
            :size="25"
          />
        </span>
        <h3>{{ $t('还没有模型') }}</h3>
        <button
          type="button"
          class="primary-button"
          @click="showProviderPicker"
        >
          {{ $t('选择模型服务') }}
        </button>
      </div>
    </div>

    <section
      v-else-if="mode === 'providers'"
      class="provider-picker"
      data-testid="provider-picker"
    >
      <button
        type="button"
        class="back-button"
        @click="closeEditor"
      >
        {{ $t('‹ 返回模型列表') }}
      </button>
      <div class="picker-heading">
        <h3>{{ $t('添加模型服务') }}</h3>
      </div>
      <label class="provider-search">
        <IconSymbol
          name="search"
          :size="16"
        />
        <input
          v-model="providerSearch"
          type="search"
          :aria-label="$t('搜索模型服务')"
          :placeholder="$t('搜索厂商、平台或中转站')"
        />
      </label>
      <p
        v-if="!filteredGroups.length"
        class="provider-empty"
        role="status"
      >
        {{ $t('未找到服务') }}
      </p>
      <div
        v-for="group in filteredGroups"
        :key="group.name"
        class="choice-group"
      >
        <h4>{{ $t(group.name) }}</h4>
        <div class="choice-grid">
          <button
            v-for="choice in group.choices"
            :key="choice.id"
            type="button"
            class="provider-choice"
            :data-provider-choice="choice.id"
            @click="beginAdd(choice)"
          >
            <span class="provider-mark compatible">{{ choice.monogram }}</span>
            <span>
              <strong>{{ $t(choice.name) }}</strong>
              <small>{{ $t(choice.description) }}</small>
            </span>
            <i>›</i>
          </button>
        </div>
      </div>
    </section>

    <ModelProfileEditor
      v-else
      :editor="editor"
      @result="result"
    />
    <p
      v-if="feedbackError"
      class="model-feedback"
      role="alert"
    >
      {{ feedbackError }}
    </p>
    <p
      v-else-if="feedback"
      class="model-feedback saved"
      role="status"
    >
      {{ feedback }}
    </p>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/model-settings';
</style>
