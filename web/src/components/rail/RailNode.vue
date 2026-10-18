<script setup lang="ts">
import { questionAnswerLabel } from '@/utils/question-presentation'
import { computed, ref, watch } from 'vue'
import type { PresentationNode } from '@/utils/rail-presentation'
import RailGlyph from './RailGlyph.vue'
import RailInk from './RailInk.vue'
import SupplementMessage from '@/components/chat-area/SupplementMessage.vue'
import RailNodeDetail from './RailNodeDetail.vue'
import SealMark from './SealMark.vue'
import ApprovalCard from '@/components/chat-area/ApprovalCard.vue'
import QuestionCard from '@/components/interact/QuestionCard.vue'
import { useMemoryStore } from '@/stores/memory'
import { useRunStore } from '@/stores/run'
import { t } from '@/i18n'
import { pendingItem } from '@/utils/rail-presentation'
import { approvalDescription } from '@/utils/tool-presentation'
const props = defineProps<{
  node: PresentationNode
  turnId: string
  canResend?: boolean
  resendInput?: (id: string) => Promise<void>
}>()
const needsAttention = computed(
  () =>
    props.node.kind === 'failed' ||
    Boolean(
      props.node.await &&
      !props.node.await.verdict &&
      !props.node.await.closed &&
      props.node.await.answer === undefined,
    ),
)
const expanded = ref(needsAttention.value || props.node.kind === 'input')
const pending = computed(() => Boolean(props.node.await && pendingItem(props.node.await)))
const title = computed(() =>
  props.node.await?.mode === 'question'
    ? t('待回答')
    : props.node.await?.mode === 'approval'
      ? approvalDescription(props.node.await).action
      : props.node.title,
)
const memory = useMemoryStore(),
  run = useRunStore()
watch(needsAttention, (attention) => {
  if (attention) expanded.value = true
})
watch(
  () =>
    Boolean(
      props.node.await &&
      (props.node.await.verdict ||
        props.node.await.closed ||
        props.node.await.answer !== undefined),
    ),
  (resolved) => {
    if (resolved) expanded.value = false
  },
)
async function undoMemory() {
  if (props.node.memoryId && (await memory.remove(props.node.memoryId)))
    run.removeMemory(props.node.memoryId)
}
async function resend(id: string) {
  await props.resendInput?.(id)
}
</script>
<template>
  <div
    :id="`rail-${turnId}-${node.id}`"
    class="rail-node"
    :class="[
      node.kind,
      { running: node.running, waiting: pending, 'input-queued': node.input?.status === 'queued' },
    ]"
    :data-await-anchor="node.await?.id"
    :data-order="
      node.await?.seq ? 'sequence' : node.await?.time ? 'time' : node.await ? 'fallback' : undefined
    "
  >
    <RailInk
      v-if="node.running && !pending"
      active
    />
    <button
      class="node-button"
      :data-rail-node="`${turnId}:${node.id}`"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span class="node-glyph">
        <RailGlyph
          :kind="node.kind"
          :waiting="pending"
          :running="node.running && !pending"
        />
      </span>
      <span class="node-title">{{ title }}</span>
      <SealMark
        v-if="node.await?.verdict"
        :verdict="node.await.verdict"
      />
      <span
        v-else
        class="disclosure"
        aria-hidden="true"
      >
        {{ expanded ? '−' : '+' }}
      </span>
    </button>
    <div
      v-if="expanded"
      class="node-body"
    >
      <SupplementMessage
        v-if="node.input"
        inline
        :input="node.input"
        :can-resend="Boolean(canResend && resendInput)"
        :resend="() => resend(node.input!.id)"
      />
      <RailNodeDetail
        v-if="node.steps.length && !pending"
        :steps="node.steps"
      />
      <ApprovalCard
        v-if="node.await?.mode === 'approval' && pending"
        :item="node.await"
      />
      <QuestionCard
        v-if="node.await?.mode === 'question' && pending"
        :item="node.await"
      />
      <p v-if="node.await?.answer !== undefined">
        {{ $t('已回答：{0}', [questionAnswerLabel(node.await)]) }}
      </p>
      <p v-else-if="node.await?.closed && !node.await.verdict">
        {{ $t('已关闭') }}
      </p>
      <p v-if="node.await?.note">
        {{ node.await.note }}
      </p>
      <button
        v-if="node.memoryId"
        class="secondary-button"
        @click="undoMemory"
      >
        {{ $t('撤销这条记忆') }}
      </button>
    </div>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.rail-node {
  position: relative;
}
.node-button {
  @include mixins.interactive-row;
  display: flex;
  width: 100%;
  gap: var(--space-4);
  align-items: center;
  min-height: var(--rail-node-height);
  padding: 0;
  border: 0;
  text-align: left;
  background: var(--transparent);
  color: var(--ink-2);
  font-size: var(--text-sm);
}
.node-button[aria-expanded='true'] {
  color: var(--ink);
  background: var(--paper-2);
}
.node-button[aria-expanded='true'] .disclosure {
  color: var(--accent);
}
.node-glyph {
  display: grid;
  width: var(--rail-width);
  flex: none;
  place-content: center;
  color: var(--ink-2);
  position: relative;
  z-index: var(--z-surface);
  background: var(--paper);
}
.node-title {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.disclosure {
  color: var(--ink-3);
  font-size: var(--text-xs);
}
.node-body {
  margin-left: calc(var(--rail-width) + var(--space-4));
}
.running .node-glyph {
  color: var(--accent);
}
.await .node-glyph {
  color: var(--wait);
}
.waiting .node-glyph {
  color: var(--wait);
}
.waiting .node-glyph {
  color: var(--wait);
}
.failed .node-glyph {
  color: var(--seal);
}
.input-queued .node-glyph {
  color: var(--wait);
}
.input .node-glyph {
  color: var(--accent);
}
.input.input-queued .node-glyph {
  color: var(--wait);
}
.memory .node-glyph {
  color: var(--ink-2);
}
</style>
