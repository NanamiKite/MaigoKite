<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Approval } from '@/api/client'
import type { AwaitItem, Verdict } from '@/api/events'
import {
  approvalDescription,
  approvalScopeDescription,
  approvalScopeLabel,
} from '@/utils/tool-presentation'
import IconSymbol from '@/components/common/IconSymbol.vue'
import SealMark from './SealMark.vue'
import { questionAnswerLabel, questionCaption } from '@/utils/question-presentation'
const props = defineProps<{ item: AwaitItem; record?: Approval }>()
const opened = ref(false)
const description = computed(() => approvalDescription(props.item, props.record))
const verdict = computed<Verdict | undefined>(() => {
  if (props.item.verdict) return props.item.verdict
  const status = props.record?.status
  if (status === 'approved' || status === 'consumed') return 'allow'
  if (status === 'rejected' || status === 'denied') return 'deny'
  return undefined
})
const status = computed(() => {
  if (props.item.mode === 'question')
    return props.item.answer !== undefined ? '已回答' : props.item.closed ? '已结束' : '待回答'
  return verdict.value === 'deny'
    ? '已驳回'
    : verdict.value
      ? '已批准'
      : props.item.closed || props.record?.status === 'expired'
        ? '已结束'
        : '等待确认'
})
const scope = computed(() =>
  approvalScopeLabel(props.record?.authorization_scope || 'once', props.record?.reuse_scope),
)
const rawDetails = computed(
  () => (props.record ? JSON.stringify(props.record.arguments, null, 2) : props.item.detail) || '',
)
function toggle(event: Event) {
  if (event.currentTarget instanceof HTMLDetailsElement) opened.value = event.currentTarget.open
}
</script>
<template>
  <details
    class="receipt-entry"
    @toggle="toggle"
  >
    <summary>
      <SealMark
        v-if="item.mode === 'approval' && verdict"
        :verdict="verdict"
      />
      <IconSymbol
        v-else
        name="pending"
        :size="14"
      />
      <span class="approval-title">
        <span>
          {{ item.mode === 'approval' ? description.action : questionCaption(item) }}
        </span>
        <small v-if="item.mode === 'approval'">{{ description.target }}</small>
      </span>
      <small class="approval-status">{{ $t(status) }}</small>
      <IconSymbol
        name="chevron"
        :size="12"
        class="entry-chevron"
      />
    </summary>
    <template v-if="opened">
      <p v-if="description.command && item.mode === 'approval'">
        <code>{{ description.command }}</code>
      </p>
      <p v-if="item.answer !== undefined">
        {{ questionAnswerLabel(item) }}
      </p>
      <p v-if="item.note || record?.note">
        {{ item.note || record?.note }}
      </p>
      <template v-if="record?.authorization_scope && verdict && verdict !== 'deny'">
        <p>{{ $t('批准范围') }} · {{ scope }}</p>
        <p
          v-for="line in approvalScopeDescription(record.reuse_scope, record.authorization_scope)"
          :key="line"
        >
          {{ line }}
        </p>
      </template>
      <details v-if="rawDetails">
        <summary>{{ $t('操作详情') }}</summary>
        <pre>{{ rawDetails.slice(0, 20_000) }}</pre>
        <p v-if="rawDetails.length > 20_000">
          {{ $t('参数较长，仅显示前 20,000 个字符。') }}
        </p>
      </details>
      <p v-if="item.risk || record?.risk_level">
        {{ $t('风险等级：{0}', [item.risk || record?.risk_level]) }}
      </p>
    </template>
  </details>
</template>
<style scoped lang="scss">
@use '@/styles/receipt-details';
.approval-title {
  display: grid;
  gap: var(--space-1);
}
.approval-title small {
  color: var(--ink-2);
  font-size: var(--text-xs);
  font-family: var(--font-mono);
}
.approval-status {
  flex: none;
  color: var(--ink-2);
  font-size: var(--text-xs);
}
</style>
