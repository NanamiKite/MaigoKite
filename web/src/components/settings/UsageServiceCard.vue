<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { UsageGroup } from '@/utils/usage-presentation'
import { usageNumber } from '@/utils/usage-presentation'
import IconSymbol from '@/components/common/IconSymbol.vue'
import UsageBalance from './UsageBalance.vue'

const props = defineProps<{ group: UsageGroup; initiallyOpen?: boolean; showCache?: boolean }>()
const { locale, t } = useI18n()
const hasCache = computed(() => props.showCache ?? props.group.metrics.cache_reported_calls > 0)
const expanded = ref(new Set<string>())
const moreFields = [
  { field: 'total_tokens', label: '总 Token' },
  { field: 'reasoning_tokens', label: '推理 Token' },
  { field: 'cache_creation_tokens', label: '缓存写入' },
  { field: 'cache_miss_tokens', label: '缓存未命中' },
] as const
function percent(value: number | null) {
  return value === null
    ? '—'
    : new Intl.NumberFormat(locale.value, {
        style: 'percent',
        maximumFractionDigits: 1,
      }).format(value / 100)
}
function hasDetails(metrics: UsageGroup['metrics']) {
  return (
    metrics.failed_calls > 0 || moreFields.some((item) => metrics.tokens[item.field] !== undefined)
  )
}
function toggleDetails(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
}
function detailId(id: string) {
  return `usage-detail-${encodeURIComponent(props.group.id)}-${encodeURIComponent(id)}`
}
</script>

<template>
  <details
    class="usage-service"
    :open="initiallyOpen"
  >
    <summary :aria-label="$t('展开或收起 {0} 的用量', [group.name])">
      <span
        class="usage-service-mark"
        aria-hidden="true"
      >
        {{ group.monogram }}
      </span>
      <span class="usage-service-name">
        <strong>{{ group.name }}</strong>
        <small>
          {{ $t(group.providerName) }}
          <template v-if="group.location">· {{ group.location }}</template>
        </small>
      </span>
      <span class="usage-service-summary">
        <b>{{ usageNumber(group.metrics.calls, locale, false) }}</b>
        <small>{{ $t('次调用') }}</small>
      </span>
      <IconSymbol
        name="chevron"
        :size="16"
      />
    </summary>
    <div class="usage-service-body">
      <div class="usage-table-wrap">
        <table
          class="usage-models"
          :data-cache="hasCache"
          :aria-label="`${group.name} · ${$t('用量统计')}`"
        >
          <colgroup>
            <col class="usage-col-model" />
            <col class="usage-col-calls" />
            <col class="usage-col-tokens" />
            <col class="usage-col-tokens" />
            <col
              v-if="hasCache"
              class="usage-col-cache"
            />
            <col
              v-if="hasCache"
              class="usage-col-rate"
            />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">
                {{ $t('模型') }}
              </th>
              <th scope="col">
                {{ $t('调用次数') }}
              </th>
              <th scope="col">
                {{ $t('输入 Token') }}
              </th>
              <th scope="col">
                {{ $t('输出 Token') }}
              </th>
              <th
                v-if="hasCache"
                scope="col"
              >
                {{ $t('缓存读取') }}
              </th>
              <th
                v-if="hasCache"
                scope="col"
              >
                {{ $t('命中率') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <template
              v-for="(row, index) in group.models"
              :key="row.id"
            >
              <tr
                class="usage-model-row"
                :data-alternate="index % 2 === 1"
                :data-expanded="expanded.has(row.id)"
              >
                <th scope="row">
                  <button
                    v-if="hasDetails(row.metrics)"
                    type="button"
                    class="usage-model-toggle"
                    :title="row.model"
                    :aria-label="$t('展开或收起 {0} 的用量', [row.model])"
                    :aria-expanded="expanded.has(row.id)"
                    :aria-controls="expanded.has(row.id) ? detailId(row.id) : undefined"
                    @click="toggleDetails(row.id)"
                  >
                    <IconSymbol
                      name="chevron"
                      :size="14"
                    />
                    <span>{{ row.model }}</span>
                  </button>
                  <span
                    v-else
                    class="usage-model-name"
                    :title="row.model"
                  >
                    {{ row.model }}
                  </span>
                  <small
                    v-if="!row.metrics.calls"
                    class="usage-coverage"
                  >
                    {{ $t('尚未调用') }}
                  </small>
                  <small
                    v-if="row.metrics.reported_calls < row.metrics.calls"
                    class="usage-coverage"
                  >
                    {{ $t('用量回执 {0}/{1}', [row.metrics.reported_calls, row.metrics.calls]) }}
                  </small>
                </th>
                <td
                  :data-label="$t('调用次数')"
                  :title="usageNumber(row.metrics.calls, locale, false)"
                >
                  <span class="usage-cell-value">{{ usageNumber(row.metrics.calls, locale) }}</span>
                </td>
                <td
                  :data-label="$t('输入 Token')"
                  :title="usageNumber(row.metrics.tokens.input_tokens, locale, false)"
                >
                  <span class="usage-cell-value">
                    {{ usageNumber(row.metrics.tokens.input_tokens, locale) }}
                  </span>
                </td>
                <td
                  :data-label="$t('输出 Token')"
                  :title="usageNumber(row.metrics.tokens.output_tokens, locale, false)"
                >
                  <span class="usage-cell-value">
                    {{ usageNumber(row.metrics.tokens.output_tokens, locale) }}
                  </span>
                </td>
                <td
                  v-if="hasCache"
                  :data-label="$t('缓存读取')"
                  :title="
                    t('缓存读取 {0} Token', [
                      usageNumber(row.metrics.tokens.cache_read_tokens, locale, false),
                    ])
                  "
                >
                  <span class="usage-cell-value">
                    {{ usageNumber(row.metrics.tokens.cache_read_tokens, locale) }}
                  </span>
                </td>
                <td
                  v-if="hasCache"
                  :data-label="$t('命中率')"
                >
                  <span class="usage-cell-value">{{ percent(row.metrics.cache_pct) }}</span>
                </td>
              </tr>
              <tr
                v-if="expanded.has(row.id) && hasDetails(row.metrics)"
                class="usage-details-row"
              >
                <td :colspan="hasCache ? 6 : 4">
                  <div
                    :id="detailId(row.id)"
                    class="usage-model-detail"
                    role="region"
                    :aria-label="row.model"
                  >
                    <p class="usage-detail-model">
                      {{ row.model }}
                    </p>
                    <dl>
                      <template
                        v-for="item in moreFields"
                        :key="item.field"
                      >
                        <div v-if="row.metrics.tokens[item.field] !== undefined">
                          <dt>{{ $t(item.label) }}</dt>
                          <dd>{{ usageNumber(row.metrics.tokens[item.field], locale, false) }}</dd>
                        </div>
                      </template>
                      <div v-if="row.metrics.failed_calls">
                        <dt>{{ $t('失败调用') }}</dt>
                        <dd>{{ row.metrics.failed_calls }}</dd>
                      </div>
                    </dl>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <UsageBalance
        v-if="group.profiles.some((profile) => profile.quota_service)"
        :profiles="group.profiles"
      />
    </div>
  </details>
</template>
