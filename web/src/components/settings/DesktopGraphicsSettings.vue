<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import { desktopEffects } from '@/composables/useElectron'
import { useUIStore } from '@/stores/ui'
import type { GraphicsStatus } from '@/electron/graphics-status'
import { t } from '@/i18n'
import InkLoader from '@/components/common/InkLoader.vue'

const ui = useUIStore()
const status = shallowRef<GraphicsStatus | null>(null)
const loading = ref(false),
  saving = ref(false),
  saved = ref(false),
  error = ref('')
const restartRequired = computed(
  () => status.value && status.value.preferredEnabled !== status.value.enabledAtStartup,
)
const features = computed(() => [
  { label: 'GPU 合成', value: status.value?.features.compositing },
  { label: '页面光栅化', value: status.value?.features.rasterization },
  { label: 'WebGL', value: status.value?.features.webgl },
])
const labels: Record<string, string> = {
  enabled: '硬件加速',
  enabled_force: '硬件加速（所有页面）',
  enabled_readback: '硬件加速（性能受限）',
  enabled_on: '已启用',
  enabled_force_on: '强制启用',
  disabled_software: '软件渲染（加速已关闭）',
  unavailable_software: '软件渲染（加速不可用）',
  disabled_off: '已停用',
  disabled_off_ok: '已停用',
  unavailable_off: '不可用',
  unavailable_off_ok: '不可用',
}
function featureLabel(value: string | null | undefined) {
  if (!status.value || status.value.state === 'pending') return t('正在检测…')
  if (status.value.state === 'unavailable') return t('暂时不可用')
  return t((value && labels[value]) || '未知')
}
let unsubscribe: (() => void) | undefined
let revision = 0,
  disposed = false
async function refresh() {
  if (!desktopEffects || loading.value) return
  loading.value = true
  error.value = ''
  const requestedRevision = revision
  try {
    const value = await desktopEffects.getGraphicsStatus()
    // A pushed GPU update takes precedence over an older in-flight response.
    if (!disposed && requestedRevision === revision) status.value = value
  } catch {
    if (!disposed) error.value = t('无法读取图形状态，请重试。')
  } finally {
    loading.value = false
  }
}
async function changeAcceleration(event: Event) {
  const input = event.target as HTMLInputElement
  if (!status.value || saving.value) return
  saving.value = true
  saved.value = false
  error.value = ''
  try {
    await ui.setHardwareAcceleration(input.checked)
    saved.value = true
    await refresh()
  } catch {
    error.value = t('硬件加速设置未保存，请重试。')
  } finally {
    saving.value = false
    input.checked = status.value?.preferredEnabled ?? true
  }
}
onMounted(() => {
  unsubscribe = desktopEffects?.onGraphicsStatus((value) => {
    revision += 1
    status.value = value
  })
  void refresh()
})
onUnmounted(() => {
  disposed = true
  unsubscribe?.()
})
</script>

<template>
  <section
    class="setting-group desktop-graphics"
    data-settings-field="hardware-acceleration"
  >
    <div class="graphics-heading">
      <h3>{{ $t('图形加速') }}</h3>
      <button
        type="button"
        class="secondary-button"
        :disabled="loading || saving"
        @click="refresh"
      >
        {{ loading ? $t('刷新中…') : $t('刷新') }}
      </button>
    </div>
    <label class="setting-row">
      <span>{{ $t('使用硬件加速') }}</span>
      <input
        type="checkbox"
        :checked="status?.preferredEnabled ?? true"
        :disabled="!status || saving"
        aria-describedby="graphics-save-status"
        @change="changeAcceleration"
      />
    </label>
    <p
      id="graphics-save-status"
      class="graphics-feedback"
      aria-live="polite"
      :class="{ pending: restartRequired, 'field-saved': saved && !restartRequired }"
    >
      {{
        restartRequired
          ? $t('已保存，请从托盘退出应用后重新打开。')
          : saved
            ? $t('已保存')
            : $t('更改后需退出应用并重新打开。')
      }}
    </p>
    <p
      v-if="error"
      class="field-error"
      role="alert"
    >
      {{ error }}
    </p>
    <InkLoader v-if="loading && !status" />
    <h4 class="graphics-caption">
      {{ $t('本次运行') }}
    </h4>
    <dl
      class="graphics-features"
      aria-live="polite"
      aria-atomic="true"
    >
      <div
        v-for="feature in features"
        :key="feature.label"
      >
        <dt>{{ $t(feature.label) }}</dt>
        <dd :title="feature.value || undefined">
          {{ featureLabel(feature.value) }}
        </dd>
      </div>
    </dl>
  </section>
</template>

<style scoped lang="scss">
.graphics-heading,
.graphics-features > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-4);
}
.graphics-feedback,
.graphics-caption {
  margin-block: var(--space-3);
  color: var(--ink-2);
  font-size: var(--text-sm);
}
.graphics-feedback.pending {
  color: var(--wait-ink);
}
.graphics-feedback.field-saved {
  color: var(--accent-ink);
}
.graphics-caption {
  margin-top: var(--space-6);
}
.graphics-features {
  margin: 0;
}
.graphics-features > div {
  padding-block: var(--space-4);
  border-bottom: var(--hairline) solid var(--rule);
  font-size: var(--text-sm);
}
.graphics-features dd {
  margin: 0;
  color: var(--ink-2);
}
</style>
