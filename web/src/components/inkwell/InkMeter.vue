<script setup lang="ts">
import { t } from '@/i18n'
import { computed } from 'vue'
import type { ContextUsage } from '@/api/events'
import IconSymbol from '@/components/common/IconSymbol.vue'
import StatPopover from './StatPopover.vue'
const props = defineProps<{ context: ContextUsage; configuredOutputTokens?: number }>()
defineEmits<{ refresh: [] }>()
const percent = computed(() =>
  props.context.usedTokens === null || !props.context.windowTokens
    ? null
    : (props.context.usedTokens / props.context.windowTokens) * 100,
)
const label = computed(() =>
  percent.value === null
    ? props.context.usedTokens === null
      ? props.context.windowTokens
        ? t('{0} Token 窗口', [props.context.windowTokens.toLocaleString()])
        : t('输入量未知')
      : `${props.context.usedTokens.toLocaleString()} Token`
    : `${Math.floor(percent.value * 10) / 10}%`,
)
const counts = computed(() => {
  const input = props.context.usedTokens
  const window = props.context.windowTokens
  const used =
    input === null
      ? '—'
      : `${props.context.measurement === 'estimate' ? '~ ' : ''}${input.toLocaleString()}`
  return window
    ? t('{0} / {1} Token', [used, window.toLocaleString()])
    : t('{0} Token · 窗口未知', [used])
})
const fill = computed(() =>
  percent.value === null ? null : Math.min(100, Math.max(0, percent.value)),
)
const count = (value: number | null) => (value === null ? t('未提供') : value.toLocaleString())
const configuredOutputLimit = computed(() => {
  const limit = props.configuredOutputTokens
  const reserved = props.context.outputReservedTokens
  return limit != null && reserved != null && limit !== reserved ? limit : null
})
const inputExceedsBudget = computed(() => {
  const { inputTokens, inputBudgetTokens } = props.context
  return inputTokens != null && inputBudgetTokens != null && inputTokens > inputBudgetTokens
})
const capacityReasons: Record<string, string> = {
  checking: '正在读取模型窗口',
  unknown: '尚无模型窗口数据',
  metadata_unavailable: '窗口数据获取失败',
  unsupported_metadata: '此服务未提供可用的窗口数据',
  unauthorized: '没有权限读取窗口数据',
  not_loaded: '模型尚未加载',
  model_not_found: '未找到对应模型的窗口数据',
  ambiguous_instance: '存在多个加载实例，无法确定当前窗口',
  model_catalog_unavailable: '模型目录暂时无法读取',
  cached: '使用上次获取的容量',
}
const reason = computed(() => {
  // Capacity provenance is runtime metadata, not another product setting.
  // Only an unresolved window needs a retrieval message in the normal UI.
  if (props.context.windowTokens) return ''
  const status = props.context.capacityStatus
  return !status || status === 'known'
    ? ''
    : capacityReasons[status]
      ? t(capacityReasons[status])
      : t('窗口数据状态：{0}', [status])
})
const title = computed(() =>
  [
    counts.value,
    props.context.usedTokens === null
      ? t('尚无本次请求用量')
      : t('上下文已用 {0}{1}', [
          label.value,
          props.context.measurement === 'estimate' ? t('（估算）') : '',
        ]),
    reason.value,
  ]
    .filter(Boolean)
    .join(' · '),
)
const measurementLabel = computed(() =>
  t(
    props.context.measurement === 'estimate'
      ? '输入估算'
      : props.context.measurement === 'provider'
        ? '服务端计数'
        : '计数来源未提供',
  ),
)
</script>
<template>
  <StatPopover
    :label="$t('本次请求上下文')"
    :title="title"
  >
    <template #trigger>
      <svg
        v-if="percent !== null"
        class="context-ring"
        viewBox="0 0 16 16"
        aria-hidden="true"
      >
        <circle
          class="ring-track"
          cx="8"
          cy="8"
          r="6"
        />
        <circle
          v-if="percent !== null"
          class="ring-fill"
          cx="8"
          cy="8"
          r="6"
          pathLength="100"
          :stroke-dasharray="`${fill} 100`"
        />
      </svg>
      <!-- Unknown capacity has no ratio or empty ring; keep the available count. -->
      <IconSymbol
        v-else
        name="cube"
        :size="14"
      />
      <span>{{ counts }}</span>
      <span
        v-if="percent !== null"
        class="capacity-source"
      >
        {{ label }}
      </span>
    </template>
    <dl class="stat-rows">
      <div>
        <dt>
          {{ $t('已用上下文{0}', [context.measurement === 'estimate' ? $t('（估算）') : '']) }}
        </dt>
        <dd>
          {{ context.usedTokens === null ? $t('未提供') : `${count(context.usedTokens)} Token` }}
        </dd>
      </div>
      <div>
        <dt>{{ $t('模型窗口') }}</dt>
        <dd>
          {{
            context.windowTokens === null ? $t('窗口未知') : `${count(context.windowTokens)} Token`
          }}
        </dd>
      </div>
      <div v-if="context.inputBudgetTokens != null">
        <dt>{{ $t('可用输入预算') }}</dt>
        <dd>
          {{ context.inputTokens == null ? '—' : count(context.inputTokens) }} /
          {{ count(context.inputBudgetTokens) }} Token
        </dd>
      </div>
      <div v-if="context.compactionTriggerTokens != null">
        <dt>{{ $t('自动压缩阈值') }}</dt>
        <dd>{{ count(context.compactionTriggerTokens) }} Token</dd>
      </div>
      <div v-if="context.outputReservedTokens != null">
        <dt>{{ $t('输出预留') }}</dt>
        <dd>{{ count(context.outputReservedTokens) }} Token</dd>
      </div>
      <div v-if="configuredOutputLimit !== null">
        <dt>{{ $t('配置输出上限') }}</dt>
        <dd>{{ count(configuredOutputLimit) }} Token</dd>
      </div>
      <div v-if="context.safetyMarginTokens != null">
        <dt>{{ $t('安全余量') }}</dt>
        <dd>{{ count(context.safetyMarginTokens) }} Token</dd>
      </div>
      <div>
        <dt>{{ $t('计数方式') }}</dt>
        <dd>{{ measurementLabel }}</dd>
      </div>
    </dl>
    <p
      v-if="reason"
      class="stat-note"
    >
      {{ reason }}
    </p>
    <p
      v-if="inputExceedsBudget"
      class="stat-note"
    >
      {{ $t('本次输入已超过可用预算。') }}
    </p>
    <p
      v-if="context.capacityStatus !== 'checking' && context.windowTokens === null"
      class="stat-note"
    >
      <button
        type="button"
        class="capacity-retry"
        @click="$emit('refresh')"
      >
        {{ $t('重试') }}
      </button>
      <router-link
        class="capacity-retry"
        to="/settings?tab=models"
      >
        {{ $t('填写窗口容量') }}
      </router-link>
    </p>
    <div
      v-if="percent !== null"
      class="context-track"
    >
      <i :style="{ width: `${fill}%` }" />
    </div>
  </StatPopover>
</template>
<style scoped lang="scss">
@use '@/styles/composer-stats';
.context-ring {
  width: var(--size-14);
  height: var(--size-14);
  fill: none;
  stroke-width: var(--rail-stroke);
  flex: none;
}
.capacity-source {
  color: var(--ink-2);
}
.capacity-retry {
  margin-left: var(--space-2);
  color: var(--accent);
}
.stat-rows dd {
  min-width: 0;
  overflow-wrap: anywhere;
}
.ring-track {
  stroke: var(--rule);
}
.ring-fill {
  stroke: var(--ink-2);
  transform: rotate(calc(-1 * var(--measure-90deg)));
  transform-origin: center;
}
.context-track {
  height: var(--space-2);
  margin-top: var(--space-4);
  border-radius: var(--radius-pill);
  background: var(--rule);
  overflow: hidden;
}
.context-track i {
  display: block;
  height: 100%;
  background: var(--ink-2);
}
</style>
