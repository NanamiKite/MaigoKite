<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getModelBalance, type ModelBalance, type UsageProfile } from '@/api/usage'
import InkSelect from '@/components/common/InkSelect.vue'
import InkLoader from '@/components/common/InkLoader.vue'

const props = defineProps<{ profiles: UsageProfile[] }>()
const { t, locale } = useI18n()
const supported = computed(() => props.profiles.filter((profile) => profile.quota_service))
const selected = ref(''),
  loading = ref(false),
  error = ref('')
const balance = shallowRef<ModelBalance | null>(null)
let controller: AbortController | undefined
let generation = 0
const profile = computed(() => supported.value.find((item) => item.id === selected.value))
const label = computed(() =>
  profile.value?.quota_service === 'openrouter' ? t('密钥剩余额度') : t('账户余额'),
)
function reset() {
  generation++
  controller?.abort()
  loading.value = false
  error.value = ''
  balance.value = null
}
watch(
  () => supported.value.map((item) => `${item.id}:${item.model}:${item.base_url}`).join('|'),
  () => {
    reset()
    if (!supported.value.some((item) => item.id === selected.value))
      selected.value = supported.value[0]?.id || ''
  },
  { immediate: true },
)
watch(selected, reset)
async function load() {
  if (!profile.value || loading.value) return
  const id = profile.value.id,
    current = ++generation
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  balance.value = null
  try {
    const result = await getModelBalance(id, request.signal)
    if (current === generation) balance.value = result
  } catch (reason) {
    if (!request.signal.aborted && current === generation)
      error.value = reason instanceof Error ? t(reason.message) : t('无法查询额度')
  } finally {
    if (current === generation) loading.value = false
  }
}
function money(value: { currency: string; amount: string }) {
  const number = Number(value.amount)
  return Number.isFinite(number)
    ? new Intl.NumberFormat(locale.value, {
        style: 'currency',
        currency: value.currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 8,
      }).format(number)
    : `${value.amount} ${value.currency}`
}
onDeactivated(reset)
onBeforeUnmount(reset)
</script>

<template>
  <section
    v-if="supported.length"
    class="usage-balance"
    :aria-label="label"
    :aria-busy="loading"
  >
    <div class="usage-balance-controls">
      <span>{{ label }}</span>
      <InkSelect
        v-if="supported.length > 1"
        v-model="selected"
        :disabled="loading"
        :aria-label="$t('查询额度的模型')"
        :options="supported.map((item) => ({ value: item.id, label: item.model }))"
      />
      <button
        type="button"
        class="text-button"
        :disabled="loading"
        @click="load"
      >
        {{ balance || error ? $t('刷新') : $t('查询额度') }}
      </button>
    </div>
    <InkLoader v-if="loading" />
    <div
      v-else-if="balance"
      class="usage-balance-result"
      role="status"
    >
      <strong
        v-for="value in balance.values"
        :key="value.currency"
        :title="`${value.currency} ${value.amount}`"
      >
        {{ money(value) }}
      </strong>
      <span v-if="!balance.values.length">{{ $t('服务未提供剩余额度') }}</span>
      <time :datetime="balance.checked_at">
        {{
          $t('更新于 {0}', [
            new Date(balance.checked_at).toLocaleTimeString(locale, {
              hour: '2-digit',
              minute: '2-digit',
            }),
          ])
        }}
      </time>
    </div>
    <p
      v-if="error"
      class="field-error"
      role="alert"
    >
      {{ error }}
    </p>
  </section>
</template>
