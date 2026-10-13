<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import SecretField from '@/components/common/SecretField.vue'
import Pulse from '@/components/shell/Pulse.vue'
import CollapseCard from '@/components/common/CollapseCard.vue'
import { t } from '@/i18n'
import { confirmAction } from '@/utils/confirm-action'
const settings = useSettingsStore()
const route = useRoute()
const tavilyKey = ref(''),
  googleKey = ref(''),
  googleCx = ref(''),
  busyService = ref('')
const feedback = ref<Record<string, string>>({}),
  errors = ref<Record<string, string>>({})
const failedAction = ref<Record<string, 'save' | 'remove'>>({})
const hasError = computed(() => Object.values(errors.value).some(Boolean))
const tavilyConfigured = computed(() => settings.credentials.tavily?.configured === true)
const googleConfigured = computed(() => settings.credentials.google?.configured === true)
async function save(service: 'tavily' | 'google') {
  if (busyService.value) return
  const value =
    service === 'tavily'
      ? tavilyKey.value.trim()
      : googleKey.value.trim() + ':' + googleCx.value.trim()
  if (
    service === 'tavily'
      ? !tavilyKey.value.trim()
      : !googleKey.value.trim() || !googleCx.value.trim()
  )
    return
  busyService.value = service
  feedback.value[service] = ''
  errors.value[service] = ''
  if (await settings.saveCredential(service, value)) {
    feedback.value[service] = t('已保存')
    if (service === 'tavily') tavilyKey.value = ''
    else {
      googleKey.value = ''
      googleCx.value = ''
    }
  } else {
    errors.value[service] = settings.error || t('保存失败')
    failedAction.value[service] = 'save'
  }
  busyService.value = ''
}
async function remove(service: 'tavily' | 'google') {
  if (busyService.value || !await confirmAction(t('移除搜索凭证后，对应服务将不可用。继续？'))) return
  if (busyService.value) return
  busyService.value = service
  feedback.value[service] = ''
  errors.value[service] = ''
  if (await settings.deleteCredential(service)) feedback.value[service] = t('凭证已移除')
  else {
    errors.value[service] = settings.error || t('保存失败')
    failedAction.value[service] = 'remove'
  }
  busyService.value = ''
}
</script>
<template>
  <CollapseCard
    class="search-settings settings-panel"
    data-settings-field="search-tools"
    variant="settings"
    :title="$t('联网搜索')"
    icon="search"
    :default-expanded="route.query.field === 'search-tools'"
  >
    <template v-if="busyService || hasError" #summary>
      <Pulse :state="busyService ? 'acting' : 'error'" />
      <span>{{ busyService ? $t('正在保存…') : $t('操作失败') }}</span>
    </template>
    <article class="search-service">
      <header>
        <Pulse state="idle" /><strong>DuckDuckGo</strong><span>{{ $t('无需配置') }}</span>
      </header>
    </article>
    <CollapseCard
      v-for="service in ['tavily', 'google'] as const"
      :key="service"
      class="search-provider"
      variant="settings"
      icon="key"
      :title="service === 'tavily' ? 'Tavily' : 'Google Programmable Search'"
      :default-expanded="route.query.field === 'search-tools'"
    >
      <template #summary>
        <Pulse :state="errors[service] ? 'error' : busyService === service ? 'acting' : 'idle'" /><span>{{
          errors[service] ? $t('操作失败') : (service === 'tavily' ? tavilyConfigured : googleConfigured) ? $t('已配置') : $t('未配置')
        }}</span>
      </template>
      <form
        class="search-form credential-form"
        :class="{ 'google-form': service === 'google' }"
        @submit.prevent="save(service)"
      >
        <label><span>API Key</span><SecretField
          v-if="service === 'tavily'"
          v-model="tavilyKey"
          :label="'Tavily API Key'"
          :disabled="!!busyService"
        /><SecretField
          v-else
          v-model="googleKey"
          :label="'Google API Key'"
          :disabled="!!busyService"
        /></label>
        <label v-if="service === 'google'"><span>{{ $t('搜索引擎 ID（CX）') }}</span><input
          v-model="googleCx"
          autocomplete="off"
          :disabled="!!busyService"
        ></label>
        <div class="search-actions">
          <button
            class="primary-button"
            :disabled="
              !!busyService ||
                (service === 'tavily' ? !tavilyKey.trim() : !googleKey.trim() || !googleCx.trim())
            "
            :aria-busy="busyService === service || undefined"
            @click.prevent="save(service)"
          >
            {{ $t('保存') }}
          </button><button
            v-if="service === 'tavily' ? tavilyConfigured : googleConfigured"
            class="danger-button"
            type="button"
            :disabled="!!busyService"
            @click="remove(service)"
          >
            {{ $t('移除') }}
          </button><span
            v-if="feedback[service]"
            class="field-saved"
            role="status"
          >{{
            feedback[service]
          }}</span>
        </div>
        <p
          v-if="errors[service]"
          class="field-error"
          role="alert"
        >
          {{ errors[service]
          }}<button
            class="text-button"
            type="button"
            :disabled="!!busyService"
            @click="failedAction[service] === 'remove' ? remove(service) : save(service)"
          >
            {{ $t('重试') }}
          </button>
        </p>
      </form>
    </CollapseCard>
  </CollapseCard>
</template>
<style scoped lang="scss">
.search-service {
  padding-bottom: var(--space-4);
}
.search-provider + .search-provider { margin-top: var(--space-4); }
.search-service header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}
.search-service header span {
  color: var(--ink-2);
  margin-inline-start: auto;
  font-size: var(--text-xs);
}
.search-form {
  display: grid;
  gap: var(--space-4);
}
.search-form label {
  display: grid;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--ink-2);
}
.search-form input {
  width: 100%;
}
.search-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}
</style>
