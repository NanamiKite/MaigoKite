<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { api, type PermissionMode } from '@/api/client'
import RailGlyph from '@/components/rail/RailGlyph.vue'
import { t } from '@/i18n'
const settings = useSettingsStore()
const draft = reactive({ default_mode: 'workspace' as PermissionMode })
const feedback = ref(''),
  error = ref('')
const supportsBoundary = ref(false),
  acknowledged = ref(false)
const needsFullAccessAcknowledgement = computed(
  () =>
    draft.default_mode === 'danger_full_access' &&
    settings.permissions.default_mode !== 'danger_full_access',
)
let disposed = false
onMounted(async () => {
  try {
    const capabilities = await api.getCapabilities()
    if (!disposed) supportsBoundary.value = Boolean(capabilities.permission_boundary_v2)
  } catch (reason) {
    if (!disposed) error.value = reason instanceof Error ? reason.message : String(reason)
  }
})
onUnmounted(() => {
  disposed = true
})
const modes = [
  {
    id: 'read_only',
    label: '只读',
    kind: 'read',
    behavior: '读取与搜索自动执行；编辑、命令和联网操作会请求批准。',
  },
  {
    id: 'workspace',
    label: '工作区',
    kind: 'act',
    behavior:
      '工作区内常规操作自动执行；命令在沙箱中运行，联网、工作区外文件与敏感操作可能需要批准。',
  },
  {
    id: 'danger_full_access',
    label: '完全访问',
    kind: 'await',
    behavior: '在本机执行命令，可访问工作区外文件和网络；不再逐项审批。',
  },
] as const
watch(
  () => settings.permissions,
  (value) => {
    draft.default_mode = value.default_mode
  },
  { immediate: true },
)
watch(
  () => draft.default_mode,
  () => {
    feedback.value = ''
    error.value = ''
    acknowledged.value = false
  },
)
async function save() {
  if (settings.saving) return
  if (
    draft.default_mode === 'danger_full_access' &&
    settings.permissions.default_mode !== 'danger_full_access' &&
    (!supportsBoundary.value || !acknowledged.value)
  ) {
    error.value = t('请先确认完全访问的风险。')
    return
  }
  error.value = ''
  feedback.value = ''
  if (await settings.savePermissionSettings({ default_mode: draft.default_mode }))
    feedback.value = t('已保存')
  else error.value = settings.error || t('保存失败')
}
</script>
<template>
  <section
    class="permission-settings settings-panel"
    data-settings-field="permission-mode"
  >
    <header class="panel-heading">
      <h2>{{ $t('执行权限') }}</h2>
    </header>
    <section class="setting-group">
      <h3>{{ $t('新对话的默认权限') }}</h3>
      <div
        class="mode-options mode-grid"
        role="group"
        :aria-label="$t('执行权限')"
      >
        <button
          v-for="mode in modes"
          :key="mode.id"
          type="button"
          :class="{
            active: draft.default_mode === mode.id,
            'high-risk': mode.id === 'danger_full_access',
          }"
          :aria-pressed="draft.default_mode === mode.id"
          :disabled="settings.saving || (mode.id === 'danger_full_access' && !supportsBoundary)"
          @click="draft.default_mode = mode.id"
        >
          <RailGlyph :kind="mode.kind" />
          <span>
            <strong>{{ $t(mode.label) }}</strong>
            <small v-if="mode.id === 'workspace'">{{ $t('推荐') }}</small>
            <p>{{ $t(mode.behavior) }}</p>
          </span>
        </button>
      </div>
    </section>
    <p class="panel-copy">
      {{ $t('完全访问不会启用被停用的工具，也不会扩大只读子 Agent 的权限。') }}
    </p>
    <p
      v-if="draft.default_mode === 'workspace_auto' || draft.default_mode === 'full_access'"
      class="panel-copy"
    >
      {{ $t('当前使用受限自动模式，可在上方选择新对话的默认权限。') }}
    </p>
    <label
      v-if="needsFullAccessAcknowledgement"
      class="panel-copy"
    >
      <input
        v-model="acknowledged"
        type="checkbox"
        :disabled="settings.saving"
      />
      {{ $t('我理解风险，同意新对话默认使用完全访问。') }}
    </label>
    <footer class="permission-actions">
      <button
        class="primary-button"
        :disabled="settings.saving"
        :aria-busy="settings.saving || undefined"
        @click="save"
      >
        {{ settings.saving ? $t('正在保存…') : $t('保存权限设置') }}
      </button>
      <span
        v-if="feedback"
        class="field-saved"
        role="status"
      >
        {{ feedback }}
      </span>
    </footer>
    <p
      v-if="error"
      class="field-error"
      role="alert"
    >
      {{ error }}
      <button
        class="text-button"
        :disabled="settings.saving"
        @click="save"
      >
        {{ $t('重试') }}
      </button>
    </p>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.mode-options {
  display: grid;
  gap: var(--space-3);
  margin-top: var(--space-5);
}
.mode-options button {
  @include mixins.interactive-row;
  display: flex;
  align-items: start;
  gap: var(--space-4);
  padding: var(--space-5);
  text-align: left;
  background: var(--paper-2);
  border: var(--hairline) solid var(--rule);
  color: var(--ink);
}
.mode-options button.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.mode-options .high-risk > svg {
  color: var(--seal);
}
.mode-options strong {
  font-size: var(--text-md);
}
.mode-options small {
  @include mixins.text-ink;
  margin-inline-start: var(--space-3);
  font-size: var(--text-xs);
}
.mode-options p {
  margin: var(--space-2) 0 0;
  color: var(--ink-2);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}
.permission-actions {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}
</style>
