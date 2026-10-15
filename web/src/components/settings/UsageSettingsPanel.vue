<script setup lang="ts">
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  ref,
  shallowRef,
  watch,
} from 'vue'
import { useI18n } from 'vue-i18n'
import { getUsageStatistics, type UsageStatistics } from '@/api/usage'
import { usageGroups, usageNumber } from '@/utils/usage-presentation'
import UsageServiceCard from './UsageServiceCard.vue'
import UsageTrend from './UsageTrend.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'
defineProps<{ loading?: boolean }>()
const emit = defineEmits<{ ready: [] }>()
const { t, locale } = useI18n()
const days = ref(7),
  fetching = ref(false),
  error = ref('')
const snapshot = shallowRef<UsageStatistics | null>(null)
const groups = computed(() => (snapshot.value ? usageGroups(snapshot.value) : []))
const periods = [
  { days: 1, label: '今天' },
  { days: 7, label: '最近 7 天' },
  { days: 30, label: '最近 30 天' },
  { days: 0, label: '全部' },
]
let controller: AbortController | undefined
let generation = 0,
  activatedOnce = false
function cancel() {
  generation++
  controller?.abort()
  fetching.value = false
}
async function load() {
  controller?.abort()
  const current = ++generation,
    request = new AbortController()
  controller = request
  fetching.value = true
  error.value = ''
  try {
    const result = await getUsageStatistics(days.value, request.signal)
    if (current === generation) snapshot.value = result
  } catch (reason) {
    if (!request.signal.aborted && current === generation)
      error.value = reason instanceof Error ? t(reason.message) : t('无法读取用量统计')
  } finally {
    if (current === generation) {
      fetching.value = false
      emit('ready')
    }
  }
}
watch(days, () => {
  snapshot.value = null
  void load()
})
onMounted(() => {
  void load()
})
onActivated(() => {
  if (activatedOnce) void load()
  activatedOnce = true
})
onDeactivated(cancel)
onBeforeUnmount(cancel)
</script>

<template>
  <section
    class="settings-panel usage-settings"
    data-settings-field="usage-statistics"
    :aria-busy="fetching"
  >
    <header class="panel-heading">
      <div>
        <h2>{{ $t('统计') }}</h2>
        <p class="usage-origin">
          {{ $t('本机用量') }}
        </p>
      </div>
      <button
        type="button"
        class="secondary-button"
        :disabled="fetching"
        @click="load"
      >
        <IconSymbol
          name="refresh"
          :size="14"
        />
        {{ $t('刷新') }}
      </button>
    </header>
    <nav
      class="usage-periods"
      :aria-label="$t('统计时段')"
    >
      <button
        v-for="period in periods"
        :key="period.days"
        type="button"
        :aria-pressed="days === period.days"
        @click="days = period.days"
      >
        {{ $t(period.label) }}
      </button>
    </nav>
    <div
      v-if="fetching && !snapshot"
      class="usage-loading"
    >
      <InkLoader />
      <InkLoader />
      <InkLoader />
    </div>
    <p
      v-if="error"
      class="field-error"
      role="alert"
    >
      {{ error }}
      <button
        type="button"
        class="text-button"
        :disabled="fetching"
        @click="load"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <template v-if="snapshot">
      <dl class="usage-ledger">
        <div>
          <dt>{{ $t('调用次数') }}</dt>
          <dd>{{ usageNumber(snapshot.totals.calls, locale, false) }}</dd>
          <small v-if="snapshot.totals.failed_calls">
            {{ $t('{0} 次失败', [snapshot.totals.failed_calls]) }}
          </small>
        </div>
        <div>
          <dt>{{ $t('输入 Token') }}</dt>
          <dd :title="usageNumber(snapshot.totals.tokens.input_tokens, locale, false)">
            {{ usageNumber(snapshot.totals.tokens.input_tokens, locale) }}
          </dd>
        </div>
        <div>
          <dt>{{ $t('输出 Token') }}</dt>
          <dd :title="usageNumber(snapshot.totals.tokens.output_tokens, locale, false)">
            {{ usageNumber(snapshot.totals.tokens.output_tokens, locale) }}
          </dd>
        </div>
        <div v-if="snapshot.totals.cache_reported_calls">
          <dt>{{ $t('缓存读取') }}</dt>
          <dd :title="usageNumber(snapshot.totals.tokens.cache_read_tokens, locale, false)">
            {{ usageNumber(snapshot.totals.tokens.cache_read_tokens, locale) }}
          </dd>
          <small v-if="snapshot.totals.cache_pct !== null">
            {{
              $t('命中率 {0}', [
                new Intl.NumberFormat(locale, {
                  style: 'percent',
                  maximumFractionDigits: 1,
                }).format(snapshot.totals.cache_pct / 100),
              ])
            }}
          </small>
        </div>
      </dl>
      <p
        v-if="snapshot.totals.reported_calls < snapshot.totals.calls"
        class="usage-coverage"
        role="status"
      >
        {{ $t('用量回执 {0}/{1}', [snapshot.totals.reported_calls, snapshot.totals.calls]) }}
      </p>
      <UsageTrend
        :daily="snapshot.daily"
        :start-date="snapshot.start_date"
        :end-date="snapshot.end_date"
      />
      <div
        v-if="!snapshot.totals.calls"
        class="usage-empty"
      >
        <IconSymbol
          name="activity"
          :size="24"
        />
        <h3>{{ $t('这段时间还没有调用记录') }}</h3>
        <button
          v-if="days"
          type="button"
          class="text-button"
          @click="days = 0"
        >
          {{ $t('查看全部用量') }}
        </button>
        <RouterLink
          v-else
          to="/chat"
        >
          {{ $t('开始对话') }}
        </RouterLink>
      </div>
      <div
        v-if="groups.length"
        class="usage-services"
      >
        <header>
          <h3>{{ $t('按供应商配置') }}</h3>
          <span>{{ $t('{0} 个配置', [groups.filter((group) => !group.historical).length]) }}</span>
        </header>
        <UsageServiceCard
          v-for="(group, index) in groups"
          :key="group.id"
          :group="group"
          :initially-open="index === 0"
          :show-cache="snapshot.totals.cache_reported_calls > 0"
        />
      </div>
      <RouterLink
        v-else
        class="text-button"
        to="/settings?tab=models"
      >
        {{ $t('添加模型服务') }}
      </RouterLink>
      <footer class="usage-updated">
        <time :datetime="snapshot.until">
          {{
            $t('更新于 {0}', [
              new Date(snapshot.until).toLocaleTimeString(locale, {
                hour: '2-digit',
                minute: '2-digit',
              }),
            ])
          }}
        </time>
      </footer>
    </template>
  </section>
</template>

<style lang="scss">
@use '@/styles/usage-settings';
</style>
