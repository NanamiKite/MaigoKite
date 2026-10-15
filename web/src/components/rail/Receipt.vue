<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Turn } from '@/api/events'
import { formatDuration } from '@/utils/format'
import { useSessionStore } from '@/stores/session'
import { useWorkspaceStore } from '@/stores/workspace'
import { changedFiles, commandSteps } from '@/utils/receipt-details'
import { summarize } from '@/stores/run-projection'
import IconSymbol from '@/components/common/IconSymbol.vue'
import ReceiptDetails from './ReceiptDetails.vue'
import RailGlyph from './RailGlyph.vue'
const props = defineProps<{ turn: Turn; embedded?: boolean }>()
const summary = computed(
  () => props.turn.summary || (props.embedded ? summarize(props.turn) : undefined),
)
const detailsId = computed(
  () => `receipt-${props.embedded ? 'drawer' : 'chat'}-${props.turn.id}-details`,
)
const session = useSessionStore(),
  workspace = useWorkspaceStore()
type Category = 'files' | 'commands' | 'approvals' | 'failures'
const selected = ref<Category | null>(null)
const folderId = computed(() => {
  const conversation = session.sessions.find((item) => item.id === props.turn.sessionId)
  return conversation ? conversation.folder_id || '' : null
})
const root = computed(
  () => workspace.folders.find((item) => item.id === folderId.value)?.root_path || '',
)
const files = computed(() => changedFiles(props.turn, root.value))
const commands = computed(() => commandSteps(props.turn))
const failures = computed(() => props.turn.steps.filter((step) => step.status === 'failed'))
const facts = computed(
  () =>
    [
      {
        id: 'files',
        label: '改动文件',
        icon: 'file',
        count: Math.max(files.value.length, props.turn.summary?.files || 0),
      },
      {
        id: 'commands',
        label: '执行命令',
        icon: 'terminal',
        count: Math.max(commands.value.length, props.turn.summary?.commands || 0),
      },
      {
        id: 'approvals',
        label: '等你处理',
        icon: 'pending',
        count: Math.max(props.turn.awaits.length, props.turn.summary?.awaits || 0),
      },
      {
        id: 'failures',
        label: '失败',
        icon: 'alert',
        count: Math.max(
          failures.value.length,
          props.turn.summary?.failed || 0,
          props.turn.error ? 1 : 0,
        ),
      },
    ] as const,
)
const hasOperations = computed(() => facts.value.some((fact) => fact.count))
const title = computed(() =>
  ['running', 'waiting'].includes(props.turn.status)
    ? '执行记录'
    : props.turn.status === 'cancelled'
      ? '已停止'
      : props.turn.status === 'failed'
        ? '执行未完成'
        : hasOperations.value
          ? '执行小结'
          : '已回复',
)
function toggle(category: Category) {
  selected.value = selected.value === category ? null : category
}
function toggleSummary() {
  selected.value = selected.value ? null : (facts.value.find((fact) => fact.count)?.id ?? null)
}
</script>
<template>
  <section
    v-if="summary"
    class="receipt"
    :class="[turn.status, { compact: !hasOperations, embedded }]"
  >
    <button
      v-if="!embedded"
      type="button"
      class="end-glyph"
      :data-rail-node="turn.id + ':end'"
      :aria-expanded="hasOperations ? Boolean(selected) : undefined"
      :aria-label="hasOperations ? $t('展开或收起执行小结') : $t(title)"
      @click="toggleSummary"
    >
      <RailGlyph
        :kind="turn.status === 'failed' || turn.status === 'cancelled' ? 'failed' : 'end'"
      />
    </button>
    <div class="receipt-sheet">
      <header>
        <h3>{{ $t(title) }}</h3>
        <time>{{ formatDuration(summary.durationMs) }}</time>
      </header>
      <template v-if="hasOperations">
        <div class="receipt-facts">
          <button
            v-for="fact in facts.filter((item) => item.count)"
            :key="fact.id"
            type="button"
            :class="{ 'failed-fact': fact.id === 'failures', active: selected === fact.id }"
            :aria-expanded="selected === fact.id"
            :aria-controls="detailsId"
            @click="toggle(fact.id)"
          >
            <IconSymbol
              :name="fact.icon"
              :size="13"
            />
            <span>{{ $t(fact.label) }}</span>
            <strong>{{ fact.count }}</strong>
            <template v-if="fact.id === 'files' && (summary.added || summary.removed)">
              <small class="diff-added">+{{ summary.added }}</small>
              <small class="diff-removed">−{{ summary.removed }}</small>
            </template>
            <IconSymbol
              name="chevron"
              :size="11"
              class="fact-chevron"
            />
          </button>
        </div>
        <ReceiptDetails
          v-if="selected"
          :id="detailsId"
          :key="selected"
          :turn="turn"
          :category="selected"
          :files="files"
          :folder-id="folderId"
        />
      </template>
    </div>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/receipt';
</style>
