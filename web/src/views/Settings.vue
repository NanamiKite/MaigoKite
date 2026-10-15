<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { settingsFields, type SettingsField } from '@/config/settings-fields'
import SettingsSearch from '@/components/settings/SettingsSearch.vue'
import ModelSettingsPanel from '@/components/settings/ModelSettingsPanel.vue'
import SubagentSettingsPanel from '@/components/settings/SubagentSettingsPanel.vue'
import UsageSettingsPanel from '@/components/settings/UsageSettingsPanel.vue'
import ToolSettingsPanel from '@/components/settings/ToolSettingsPanel.vue'
import PermissionSettingsPanel from '@/components/settings/PermissionSettingsPanel.vue'
import SafetySettingsPanel from '@/components/settings/SafetySettingsPanel.vue'
import RuntimeStatusPanel from '@/components/settings/RuntimeStatusPanel.vue'
import AppearanceSettingsPanel from '@/components/settings/AppearanceSettingsPanel.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'
const route = useRoute(),
  router = useRouter(),
  settings = useSettingsStore()
const tabs = [
  { id: 'general', label: '外观与称呼', icon: 'eye', component: AppearanceSettingsPanel },
  { id: 'models', label: '模型', icon: 'brain', component: ModelSettingsPanel },
  { id: 'subagents', label: '子 Agent', icon: 'subagents', component: SubagentSettingsPanel },
  { id: 'statistics', label: '统计', icon: 'activity', component: UsageSettingsPanel },
  { id: 'tools', label: '工具', icon: 'files', component: ToolSettingsPanel },
  { id: 'permissions', label: '权限', icon: 'shield', component: PermissionSettingsPanel },
  { id: 'safety', label: '安全', icon: 'key', component: SafetySettingsPanel },
  { id: 'workspace', label: '运行状态', icon: 'activity', component: RuntimeStatusPanel },
]
const active = computed(() => tabs.find((tab) => tab.id === route.query.tab) || tabs[0])
const highlighted = ref(''),
  loading = ref(false),
  loadError = ref('')
const content = ref<HTMLElement | null>(null)
const search = ref<InstanceType<typeof SettingsSearch> | null>(null)
function selectTab(id: string) {
  void router.replace({ query: { ...route.query, tab: id, field: undefined } })
}
async function locate(field: SettingsField) {
  highlighted.value = field.id
  await router.replace({ query: { ...route.query, tab: field.tab, field: field.id } })
  await nextTick()
  const target =
    content.value?.querySelector<HTMLElement>('[data-settings-field="' + field.id + '"]') ||
    content.value?.querySelector<HTMLElement>('[data-settings-field="models"]')
  if (!target) return
  for (const detail of target.querySelectorAll<HTMLDetailsElement>('details')) detail.open = true
  target.scrollIntoView({ block: 'center', behavior: 'auto' })
  const control = target.matches('input,select,button')
    ? target
    : target.querySelector<HTMLElement>('input,select,button,summary')
  ;(control || target).focus({ preventScroll: true })
}
function panelReady() {
  const field = settingsFields.find((item) => item.id === route.query.field && item.tab === active.value.id)
  if (field) void locate(field)
}
async function load() {
  if (loading.value) return
  loading.value = true
  loadError.value = ''
  settings.error = null
  // Capture each load before another shared-store operation replaces its error.
  await settings.loadPreferences()
  if (!settings.profileLoaded) loadError.value = settings.error || ''
  settings.error = null
  await settings.loadPermissionSettings()
  if (settings.error) loadError.value ||= settings.error
  if (!settings.loaded) {
    await settings.loadSettings()
    if (!settings.loaded) loadError.value ||= settings.error || ''
  }
  loading.value = false
}
onMounted(async () => {
  await load()
  const field = settingsFields.find((item) => item.id === route.query.field)
  if (field) void locate(field)
  else if (document.activeElement === document.body) search.value?.focus?.()
})
watch(
  () => route.query.field,
  (value) => {
    const field = settingsFields.find((item) => item.id === value)
    if (field && highlighted.value !== field.id) void locate(field)
  },
)
</script>
<template>
  <main class="page-shell settings-page">
    <header class="settings-header">
      <h1 class="page-title">
        {{ $t('设置') }}
      </h1>
      <RouterLink to="/integrations">
        {{ $t('管理 MCP 与 Skill 扩展') }}
      </RouterLink>
    </header>
    <SettingsSearch
      ref="search"
      @locate="locate"
    />
    <InkLoader v-if="loading" />
    <p
      v-if="loadError"
      class="page-error"
      role="alert"
    >
      {{ loadError
      }}<button
        class="secondary-button"
        :disabled="loading"
        @click="load"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <div class="settings-layout">
      <nav
        class="settings-tabs"
        :aria-label="$t('设置分类')"
      >
        <button
          v-for="tab in tabs"
          :key="tab.id"
          :aria-pressed="active.id === tab.id"
          @click="selectTab(tab.id)"
        >
          <IconSymbol
            :name="tab.icon"
            :size="16"
          /><span>{{ $t(tab.label) }}</span>
        </button>
      </nav>
      <div
        ref="content"
        class="settings-content"
        :data-highlight="highlighted"
      >
        <KeepAlive :max="5" exclude="SafetySettingsPanel">
          <component
            :is="active.component"
            :key="active.id"
            :loading="loading"
            @ready="panelReady"
          />
        </KeepAlive>
      </div>
    </div>
  </main>
</template>
