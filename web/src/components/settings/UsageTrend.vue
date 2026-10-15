<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { UsageStatistics } from '@/api/usage'
import { usageNumber } from '@/utils/usage-presentation'
const props = defineProps<{
  daily: UsageStatistics['daily']
  startDate: string | null
  endDate: string
}>()
const { t, locale } = useI18n()
const selected = ref('')
const hovered = ref(''),
  focused = ref('')
const span = computed(() => {
  const start = props.startDate || props.daily[0]?.date || props.endDate
  return (
    Math.round(
      (Date.parse(`${props.endDate}T12:00:00Z`) - Date.parse(`${start}T12:00:00Z`)) / 86_400_000,
    ) + 1
  )
})
const days = computed(() => {
  if (!props.daily.length) return []
  const last = new Date(`${props.endDate}T12:00:00Z`)
  const count = Math.min(30, Math.max(1, span.value))
  const byDay = new Map(props.daily.map((day) => [day.date, day]))
  return Array.from({ length: count }, (_, index) => {
    const day = new Date(last)
    day.setUTCDate(day.getUTCDate() - (count - 1 - index))
    const key = day.toISOString().slice(0, 10)
    const saved = byDay.get(key)
    return {
      date: key,
      calls: saved?.calls || 0,
      input: saved?.tokens.input_tokens,
      tokens:
        saved && saved.tokens.input_tokens === undefined && saved.tokens.output_tokens === undefined
          ? undefined
          : (saved?.tokens.input_tokens || 0) + (saved?.tokens.output_tokens || 0),
    }
  })
})
const max = computed(() => Math.max(1, ...days.value.map((day) => day.tokens || 0)))
const active = computed(
  () =>
    days.value.find((day) => day.date === (hovered.value || focused.value || selected.value)) ||
    days.value[days.value.length - 1],
)
function focusDay(event: FocusEvent, date: string) {
  focused.value =
    event.currentTarget instanceof HTMLElement && event.currentTarget.matches(':focus-visible')
      ? date
      : ''
  if (focused.value) hovered.value = ''
}
function date(value: string) {
  return new Date(`${value}T12:00:00`).toLocaleDateString(locale.value, {
    month: 'short',
    day: 'numeric',
  })
}
function description(day: (typeof days.value)[number]) {
  return day.tokens === undefined
    ? t('{0}：{1} 次调用，未提供 Token 用量', [date(day.date), day.calls])
    : t('{0}：{1} 次调用，已报告 {2} Token', [
        date(day.date),
        day.calls,
        usageNumber(day.tokens, locale.value, false),
      ])
}
</script>
<template>
  <section
    v-if="days.length"
    class="usage-trend"
    :aria-label="$t('每日用量')"
  >
    <header>
      <h3>{{ $t('每日用量') }}</h3>
      <span>
        {{ $t('已报告 Token') }}
        <small v-if="span > 30">· {{ $t('最近 30 天') }}</small>
      </span>
    </header>
    <div class="usage-trend-bars">
      <button
        v-for="day in days"
        :key="day.date"
        type="button"
        :aria-label="description(day)"
        :title="description(day)"
        :data-selected="selected === day.date"
        :aria-pressed="selected === day.date"
        :style="{
          '--usage-bar-height': `${((day.tokens || 0) / max) * 100}%`,
          '--usage-input-share': `${day.tokens ? ((day.input || 0) / day.tokens) * 100 : 0}%`,
        }"
        @mouseenter="hovered = day.date"
        @mouseleave="hovered = ''"
        @focus="focusDay($event, day.date)"
        @blur="focused = ''"
        @click="selected = day.date"
      >
        <span class="usage-bar">
          <i />
          <b />
        </span>
      </button>
    </div>
    <footer>
      <time>{{ days[0] && date(days[0].date) }}</time>
      <time>{{ days[days.length - 1] && date(days[days.length - 1].date) }}</time>
    </footer>
    <div class="usage-trend-legend">
      <span>
        <i aria-hidden="true" />
        {{ $t('输入 Token') }}
      </span>
      <span>
        <i aria-hidden="true" />
        {{ $t('输出 Token') }}
      </span>
    </div>
    <p
      v-if="active"
      class="usage-trend-caption"
      role="status"
    >
      {{ description(active) }}
    </p>
  </section>
</template>
