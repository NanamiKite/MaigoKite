<script setup lang="ts">
import { t } from '@/i18n'
import { computed, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import { api } from '@/api/client'
import type { ContextUsage } from '@/api/events'
import {
  cachedModelCapacity,
  matchesModelContext,
  modelContextKey,
  preferredModelCapacity,
  rememberModelCapacity,
} from '@/api/model-context'
import type { ModelOption } from '@/config/model-groups'
import { useRunStore } from '@/stores/run'
import { useUIStore } from '@/stores/ui'
import { sessionUsage } from '@/stores/run-usage'
import { formatDuration } from '@/utils/format'
import IconSymbol from '@/components/common/IconSymbol.vue'
import StatPopover from './StatPopover.vue'
import InkMeter from './InkMeter.vue'
import CompactionStatus from './CompactionStatus.vue'
const props = defineProps<{ sessionId: string; model?: ModelOption }>()
const run = useRunStore(),
  ui = useUIStore()
const empty = sessionUsage([])
const stats = computed(() => run.metrics[props.sessionId]?.usage || empty)
const discovered = shallowRef<ContextUsage>()
let lookup: AbortController | undefined

function profileContext(selected: ModelOption, status = 'unknown'): ContextUsage {
  const capacity = selected.contextWindow
  const known = capacity !== undefined && Number.isSafeInteger(capacity) && capacity > 0
  return {
    seq: 0,
    callId: '',
    modelName: selected.model,
    modelId: selected.id,
    modelKey: JSON.stringify([
      selected.provider,
      selected.baseUrl || '',
      selected.model,
      capacity ?? null,
    ]),
    usedTokens: null,
    windowTokens: known ? capacity : null,
    measurement: null,
    capacitySource: known ? 'model_profile' : null,
    capacityStatus: known ? 'known' : status,
  }
}

async function refreshCapacity() {
  lookup?.abort()
  const selected = props.model
  if (!selected || ui.appearance.showContextUsage === false) return
  const controller = new AbortController()
  lookup = controller
  const previous = discovered.value || cachedModelCapacity(selected) || profileContext(selected)
  discovered.value = { ...previous, capacityStatus: 'checking' }
  try {
    // Capacity discovery never generates text or creates a task/client owner.
    const result = await api.getModelContext(selected.id, controller.signal)
    if (!controller.signal.aborted) {
      const resolved = matchesModelContext(result, selected)
        ? result
        : profileContext(selected, 'metadata_unavailable')
      // A successful Core query is authoritative even when it returns unknown.
      // Do not restore a discarded cross-vendor capacity from local storage.
      discovered.value = resolved
      if (resolved.windowTokens) rememberModelCapacity(resolved, selected)
    }
  } catch {
    if (!controller.signal.aborted)
      discovered.value = { ...previous, capacityStatus: 'metadata_unavailable' }
  }
}
watch(
  () => [
    props.model ? modelContextKey(props.model) : '',
    props.sessionId,
    ui.appearance.showContextUsage,
  ],
  () => {
    discovered.value = props.model ? cachedModelCapacity(props.model) : undefined
    void refreshCapacity()
  },
  { immediate: true },
)
watch(
  () => {
    const value = run.metrics[props.sessionId]?.context
    return JSON.stringify([
      props.model ? modelContextKey(props.model) : '',
      value?.modelKey,
      value?.windowTokens,
      value?.capacitySource,
      value?.capacityCheckedAt,
    ])
  },
  () => {
    const selected = props.model,
      value = run.metrics[props.sessionId]?.context
    const resolved = discovered.value
    if (
      selected &&
      value &&
      ([
        'lmstudio.loaded_instance',
        'ollama.loaded_instance',
        'provider_error',
        'provider_metadata',
        'model_profile',
        'route_config',
        'route_default',
      ].includes(value.capacitySource || '') ||
        (matchesModelContext(resolved, selected) &&
          resolved?.windowTokens === value.windowTokens &&
          resolved?.capacitySource === value.capacitySource))
    )
      rememberModelCapacity(value, selected)
  },
  { immediate: true },
)
onMounted(() => window.addEventListener('focus', refreshCapacity))
onBeforeUnmount(() => {
  lookup?.abort()
  window.removeEventListener('focus', refreshCapacity)
})
const context = computed<ContextUsage | undefined>(() => {
  const selected = props.model
  if (!selected) return undefined
  const value = run.metrics[props.sessionId]?.context
  const receipt = matchesModelContext(value, selected) ? value : undefined
  const resolved = matchesModelContext(discovered.value, selected) ? discovered.value : undefined
  // Historical usage remains useful, but its old cross-vendor capacity must
  // not defeat a fresh route lookup (including an unknown local window).
  const receiptCapacity =
    resolved &&
    ['models.dev', 'sdk_profile'].includes(receipt?.capacitySource || '') &&
    receipt?.windowTokens !== resolved.windowTokens
      ? undefined
      : receipt
  const capacity =
    preferredModelCapacity([receiptCapacity, resolved, profileContext(selected)]) ||
    resolved ||
    receiptCapacity ||
    profileContext(selected)
  const budget = receipt?.windowTokens === capacity.windowTokens ? receipt : undefined
  // Discovery is model-scoped; usage remains exclusively this conversation's.
  return {
    ...capacity,
    capacityStatus:
      resolved?.capacityStatus && !['known', 'assumed'].includes(resolved.capacityStatus)
        ? resolved.capacityStatus
        : resolved?.windowTokens &&
            resolved.capacitySource !== capacity.capacitySource &&
            ['lmstudio.loaded_instance', 'ollama.loaded_instance', 'provider_error'].includes(
              capacity.capacitySource || '',
            )
          ? 'cached'
          : capacity.capacityStatus,
    seq: receipt?.seq ?? 0,
    callId: receipt?.callId || '',
    usedTokens: receipt?.usedTokens ?? null,
    inputTokens: receipt?.inputTokens ?? null,
    measurement: receipt?.measurement ?? null,
    inputBudgetTokens: budget?.inputBudgetTokens ?? capacity.inputBudgetTokens,
    outputReservedTokens: budget?.outputReservedTokens ?? capacity.outputReservedTokens,
    safetyMarginTokens: budget?.safetyMarginTokens ?? capacity.safetyMarginTokens,
    compactionTriggerTokens: budget?.compactionTriggerTokens ?? capacity.compactionTriggerTokens,
  }
})
const turn = computed(() => (run.turns[props.sessionId] || []).slice(-1)[0])
const footerCompaction = computed(() =>
  turn.value?.compaction?.status !== 'failed' ? turn.value?.compaction : undefined,
)
const active = computed(
  () => stats.value.calls + stats.value.summaryCalls + stats.value.subagentCalls > 0,
)
const compact = (value: number) =>
  Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
const count = (value: number | undefined) =>
  value === undefined ? t('未提供') : value.toLocaleString()
const duration = (value: number | null) => (value === null ? t('未提供') : formatDuration(value))
const speed = computed(() =>
  stats.value.tokensPerSecond === null ? '—' : stats.value.tokensPerSecond.toFixed(1),
)
const cache = computed(() =>
  stats.value.cachePct === null ? '—' : `${Math.floor(stats.value.cachePct * 10) / 10}%`,
)
const cacheNote = computed(() => {
  const value = stats.value
  if (!value.totalCalls) return ''
  if (!value.cacheReportedCalls) return t('尚无可用的缓存用量，无法计算命中率。')
  if (value.cachePartial)
    return t('缓存统计不完整（{0}/{1} 次请求已报告），暂不计算命中率。', [
      value.cacheReportedCalls,
      value.totalCalls,
    ])
  if (value.cachePct === null && (value.tokens.input_tokens ?? 0) > 0)
    return t('缓存用量不一致，无法计算命中率。')
  return ''
})
const tokenLabel = computed(() =>
  stats.value.totalTokens === null
    ? '—'
    : `${stats.value.partial ? '≥ ' : ''}${compact(stats.value.totalTokens)}`,
)
</script>
<template>
  <div
    v-if="active || footerCompaction || (ui.appearance.showContextUsage !== false && context)"
    class="composer-dock"
    :aria-label="$t('对话统计')"
  >
    <template v-if="active">
      <StatPopover
        :label="$t('执行统计')"
        :title="$t('生成速度 {0} tok/s', [speed])"
      >
        <template #trigger>
          <IconSymbol
            name="activity"
            :size="14"
          />
          <span>{{ speed }} tok/s</span>
        </template>
        <dl class="stat-rows">
          <div>
            <dt>{{ $t('模型请求') }}</dt>
            <dd>{{ $t('{0} 次', [stats.calls]) }}</dd>
          </div>
          <div v-if="stats.summaryCalls">
            <dt>{{ $t('上下文摘要') }}</dt>
            <dd>{{ $t('{0} 次', [stats.summaryCalls]) }}</dd>
          </div>
          <div v-if="stats.subagentCalls">
            <dt>{{ $t('子 Agent 模型请求') }}</dt>
            <dd>{{ $t('{0} 次', [stats.subagentCalls]) }}</dd>
          </div>
          <div>
            <dt>{{ $t('模型耗时') }}</dt>
            <dd>{{ duration(stats.modelMs) }}</dd>
          </div>
          <div>
            <dt>{{ $t('工具耗时') }}</dt>
            <dd>{{ duration(stats.toolMs) }}</dd>
          </div>
          <div>
            <dt>{{ $t('平均首 Token 延迟') }}</dt>
            <dd>{{ duration(stats.firstTokenMs) }}</dd>
          </div>
          <div>
            <dt>{{ $t('平均生成速度') }}</dt>
            <dd>{{ speed }} tok/s</dd>
          </div>
        </dl>
      </StatPopover>
      <StatPopover
        :label="$t('Token 用量')"
        :title="$t('本对话已报告 {0} Token', [tokenLabel])"
      >
        <template #trigger>
          <IconSymbol
            name="cube"
            :size="14"
          />
          <span>{{ tokenLabel }} Token</span>
        </template>
        <dl class="stat-rows">
          <div>
            <dt>{{ $t('输入（含缓存）') }}</dt>
            <dd>{{ count(stats.tokens.input_tokens) }}</dd>
          </div>
          <div>
            <dt>{{ $t('输出') }}</dt>
            <dd>{{ count(stats.tokens.output_tokens) }}</dd>
          </div>
          <div>
            <dt>{{ $t('总用量') }}</dt>
            <dd>
              {{ stats.totalTokens === null ? $t('未提供') : stats.totalTokens.toLocaleString() }}
            </dd>
          </div>
          <div>
            <dt>{{ $t('缓存读取') }}</dt>
            <dd>{{ count(stats.tokens.cache_read_tokens) }}</dd>
          </div>
          <div>
            <dt>{{ $t('缓存写入') }}</dt>
            <dd>{{ count(stats.tokens.cache_creation_tokens) }}</dd>
          </div>
          <div v-if="stats.tokens.reasoning_tokens !== undefined">
            <dt>{{ $t('思考（含于输出）') }}</dt>
            <dd>{{ count(stats.tokens.reasoning_tokens) }}</dd>
          </div>
        </dl>
        <p
          v-if="stats.partial"
          class="stat-note"
        >
          {{ $t('用量不完整') }}
        </p>
        <p
          v-if="cacheNote"
          class="stat-note"
        >
          {{ cacheNote }}
        </p>
      </StatPopover>
      <StatPopover
        :label="$t('缓存命中率')"
        :title="cacheNote || $t('缓存命中 {0}', [cache])"
      >
        <template #trigger>
          <IconSymbol
            name="bolt"
            :size="14"
          />
          <span>{{ $t('缓存 {0}', [cache]) }}</span>
        </template>
        <dl class="stat-rows">
          <div>
            <dt>{{ $t('命中率') }}</dt>
            <dd>{{ cache }}</dd>
          </div>
          <div>
            <dt>{{ $t(stats.cachePartial ? '已报告命中输入 Token' : '命中输入 Token') }}</dt>
            <dd>{{ count(stats.tokens.cache_read_tokens) }}</dd>
          </div>
          <div>
            <dt>{{ $t(stats.cachePartial ? '已报告总输入 Token' : '总输入 Token') }}</dt>
            <dd>{{ count(stats.tokens.input_tokens) }}</dd>
          </div>
          <div>
            <dt>{{ $t('已报告缓存用量的请求') }}</dt>
            <dd>{{ $t('{0}/{1} 次', [stats.cacheReportedCalls, stats.totalCalls]) }}</dd>
          </div>
        </dl>
        <p
          v-if="cacheNote"
          class="stat-note"
        >
          {{ cacheNote }}
        </p>
      </StatPopover>
    </template>
    <InkMeter
      v-if="ui.appearance.showContextUsage !== false && context"
      :context="context"
      :configured-output-tokens="model?.maxTokens"
      @refresh="refreshCapacity"
    />
    <CompactionStatus
      v-if="turn && footerCompaction"
      :compaction="footerCompaction"
      :turn-status="turn.status"
      compact
      announce
    />
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/composer-stats';
</style>
