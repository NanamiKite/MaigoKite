<script setup lang="ts">
import { t } from '@/i18n'
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, type ToolSetting } from '@/api/client'
import { builtinToolCopy } from '@/config/tool-copy'
import SearchToolSettingsPanel from './SearchToolSettingsPanel.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import CollapseCard from '@/components/common/CollapseCard.vue'
const route = useRoute()
const emit = defineEmits<{ ready: [] }>()
const tools = ref<ToolSetting[]>([]),
  draft = ref<Record<string, boolean>>({})
const loading = ref(false),
  saving = ref(false),
  error = ref(''),
  message = ref('')
const failedAction = ref<'load' | 'save'>('load')
const dirty = computed(() => tools.value.some((tool) => draft.value[tool.name] !== tool.enabled))
const presentedTools = computed(() => tools.value.map(tool => ({
  ...tool,
  copy: tool.source === 'builtin' && Object.prototype.hasOwnProperty.call(builtinToolCopy, tool.name)
    ? builtinToolCopy[tool.name] : undefined,
})))
const groups = computed(() => [
  { title: '内置工具', icon: 'files', tools: presentedTools.value.filter((tool) => tool.source === 'builtin') },
  { title: '外部工具', icon: 'globe', tools: presentedTools.value.filter((tool) => tool.source === 'mcp') },
].map(group => ({ ...group, enabled: group.tools.filter(tool => draft.value[tool.name]).length })))
function apply(items: ToolSetting[]) {
  tools.value = items
  draft.value = Object.fromEntries(items.map((tool) => [tool.name, tool.enabled]))
}
async function load() {
  loading.value = true
  error.value = ''
  failedAction.value = 'load'
  try {
    apply((await api.getToolSettings()).tools)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    loading.value = false
    emit('ready')
  }
}
async function save() {
  if (saving.value || !dirty.value) return
  saving.value = true
  error.value = ''
  message.value = ''
  failedAction.value = 'save'
  const changes = Object.fromEntries(
    tools.value
      .filter((tool) => draft.value[tool.name] !== tool.enabled)
      .map((tool) => [tool.name, draft.value[tool.name]]),
  )
  try {
    apply((await api.updateToolSettings(changes)).tools)
    message.value = t('已保存')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    saving.value = false
  }
}
onMounted(() => {
  void load()
})
</script>
<template>
  <div class="tools-panels">
    <section
      class="selection settings-panel"
      data-settings-field="tool-selection"
      :aria-busy="loading || saving"
    >
      <header class="panel-heading">
        <h2>{{ $t('助手可用工具') }}</h2>
      </header>
      <div
        v-if="loading"
        class="panel-loading"
      >
        <InkLoader /><InkLoader /><span class="sr-only">{{ $t('正在读取工具…') }}</span>
      </div>
      <p
        v-if="error"
        class="field-error"
        role="alert"
      >
        {{ error
        }}<button
          class="secondary-button"
          :disabled="loading || saving"
          @click="failedAction === 'save' ? save() : load()"
        >
          {{ $t('重试') }}
        </button>
      </p>
      <template v-if="!loading && tools.length">
        <div class="tool-groups">
          <CollapseCard
            v-for="group in groups"
            :key="group.title"
            variant="settings"
            :title="$t(group.title)"
            :icon="group.icon"
            :default-expanded="route.query.field === 'tool-selection'"
          >
            <template #summary>{{ $t('已启用 {0}/{1}', [group.enabled, group.tools.length]) }}</template>
            <p
              v-if="!group.tools.length"
              class="panel-copy"
            >
              {{ $t('暂无已连接的工具。') }}
              <RouterLink to="/integrations">
                {{ $t('查看技能与外部连接') }}
              </RouterLink>
            </p>
            <article
              v-for="tool in group.tools"
              :key="tool.name"
              class="tool-entry"
            >
              <label class="tool-row"><span><strong>{{ tool.copy ? $t(tool.copy.title) : tool.name }}</strong><small>{{ tool.copy ? $t(tool.copy.summary) : $t('暂无说明') }}</small><small v-if="!tool.configurable">{{ $t('当前不可用') }}</small></span><input
                v-model="draft[tool.name]"
                type="checkbox"
                :disabled="saving || !tool.configurable"
                :aria-label="tool.copy ? $t(tool.copy.title) : tool.name"
              ></label>
              <details v-if="tool.copy || tool.description.trim()" class="tool-instructions">
                <summary>{{ tool.copy ? $t('工具说明') : tool.source === 'mcp' ? $t('服务说明（原文）') : $t('模型指令（原文）') }}</summary>
                <template v-if="tool.copy">
                  <p class="tool-description">{{ $t(tool.copy.details) }}</p>
                  <details v-if="tool.description.trim()" class="tool-original">
                    <summary>{{ $t('模型指令（原文）') }}</summary>
                    <pre>{{ tool.description }}</pre>
                  </details>
                </template>
                <pre v-else>{{ tool.description }}</pre>
              </details>
            </article>
          </CollapseCard>
        </div>
        <footer class="settings-actions">
          <RouterLink to="/integrations">
            {{ $t('查看技能与外部连接') }}
          </RouterLink>
          <div>
            <span
              v-if="message && !dirty"
              class="field-saved"
              role="status"
            >{{ message }}</span><button
              class="primary-button"
              :disabled="saving || !dirty"
              :aria-busy="saving || undefined"
              @click="save"
            >
              {{ saving ? $t('正在保存…') : $t('保存工具设置') }}
            </button>
          </div>
        </footer>
      </template>
      <p
        v-else-if="!loading && !error"
        class="panel-empty"
      >
        {{ $t('没有可用工具，连接外部服务后可在这里管理。') }}
        <RouterLink to="/integrations">
          {{ $t('添加 MCP') }}
        </RouterLink>
      </p>
    </section>
    <SearchToolSettingsPanel />
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/tool-settings';
</style>
