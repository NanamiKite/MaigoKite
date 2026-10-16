<script setup lang="ts">
import { computed, watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { useWorkspaceStore } from '@/stores/workspace'
import Pulse from '@/components/shell/Pulse.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import DesktopGraphicsSettings from './DesktopGraphicsSettings.vue'
import { desktopEffects } from '@/composables/useElectron'
const settings = useSettingsStore(),
  workspace = useWorkspaceStore()
const status = computed(() => settings.runtimeStatus)
watch(
  () => workspace.selectedId,
  (id) => void settings.loadRuntimeStatus(id),
  { immediate: true },
)
</script>
<template>
  <section
    class="runtime-settings settings-panel"
    data-settings-field="runtime-status"
  >
    <header class="panel-heading">
      <h2>{{ $t('运行状态') }}</h2>
      <button
        class="secondary-button"
        :disabled="settings.runtimeLoading"
        @click="settings.loadRuntimeStatus(workspace.selectedId)"
      >
        {{ settings.runtimeLoading ? $t('刷新中…') : $t('刷新') }}
      </button>
    </header>
    <DesktopGraphicsSettings v-if="desktopEffects" />
    <div
      v-if="settings.runtimeError"
      class="field-error"
      role="alert"
    >
      {{ settings.runtimeError }}
      <button
        class="secondary-button"
        @click="settings.loadRuntimeStatus(workspace.selectedId)"
      >
        {{ $t('重试') }}
      </button>
    </div>
    <div
      v-else-if="settings.runtimeLoading"
      class="runtime-loading"
    >
      <InkLoader />
      <InkLoader />
      <span class="sr-only">{{ $t('正在读取运行状态…') }}</span>
    </div>
    <template v-else-if="status">
      <section class="setting-group">
        <h3>{{ $t('工作区位置') }}</h3>
        <code class="workspace-path">{{ status.workspaceRoot || $t('未指定工作区') }}</code>
      </section>
      <section class="setting-group">
        <h3>{{ $t('服务与版本') }}</h3>
        <article
          v-for="service in [{ label: '应用服务', value: status.backend }]"
          :key="service.label"
          class="runtime-service"
        >
          <Pulse :state="service.value.status === 'serving' ? 'idle' : 'offline'" />
          <strong>{{ $t(service.label) }}</strong>
          <code>{{ service.value.version || $t('版本未知') }}</code>
          <span>{{ service.value.status === 'serving' ? $t('运行中') : $t('不可用') }}</span>
        </article>
      </section>
      <p
        v-if="status.restart.pending"
        class="runtime-wait pending"
      >
        {{ $t('重启应用后生效。') }}
      </p>
    </template>
    <p
      v-else
      class="panel-copy"
    >
      {{ $t('尚未读取运行状态。') }}
      <button
        class="secondary-button"
        @click="settings.loadRuntimeStatus(workspace.selectedId)"
      >
        {{ $t('刷新') }}
      </button>
    </p>
  </section>
</template>
<style scoped lang="scss">
.runtime-loading {
  display: grid;
  gap: var(--space-5);
  padding-block: var(--space-7);
}
.workspace-path {
  display: block;
  margin-top: var(--space-4);
  padding: var(--space-4);
  border: var(--hairline) solid var(--rule);
  background: var(--paper-2);
  border-radius: var(--radius-sm);
  overflow-wrap: anywhere;
  font-family: var(--font-mono);
  font-size: var(--text-sm);
}
.runtime-service {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
  padding-block: var(--space-5);
  border-bottom: var(--hairline) solid var(--rule);
  font-size: var(--text-sm);
}
.runtime-service code {
  color: var(--ink-2);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  margin-inline-start: auto;
}
.runtime-service span {
  color: var(--ink-2);
}
.runtime-wait {
  color: var(--wait-ink);
  padding: var(--space-4);
  background: var(--wait-soft);
  font-size: var(--text-sm);
}
</style>
