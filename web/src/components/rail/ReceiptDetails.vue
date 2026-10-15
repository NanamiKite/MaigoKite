<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, shallowRef } from 'vue'
import { api, type Approval } from '@/api/client'
import type { AwaitItem, Turn } from '@/api/events'
import { t } from '@/i18n'
import { useUIStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import { useSessionStore } from '@/stores/session'
import { commandSteps, commandText } from '@/utils/receipt-details'
import IconSymbol from '@/components/common/IconSymbol.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import RailNodeDetail from './RailNodeDetail.vue'
import ReceiptApproval from './ReceiptApproval.vue'
const props = defineProps<{
  turn: Turn
  category: 'files' | 'commands' | 'approvals' | 'failures'
  files: { path: string; relative: string | null }[]
  folderId: string | null
}>()
const ui = useUIStore(),
  workspace = useWorkspaceStore(),
  session = useSessionStore()
const opened = ref(new Set<string>())
const history = shallowRef<Approval[]>([])
const approvalLoading = ref(false),
  approvalError = ref(''),
  approvalPartial = ref(false)
let approvalRequest: AbortController | undefined
const records = computed(
  () =>
    new Map(
      [...(session.approvals[props.turn.sessionId] || []), ...history.value]
        .filter((record) => record.task_id === props.turn.id)
        .map((record) => [record.id, record]),
    ),
)
// The pending-approval store deliberately drops decided items. Read the existing
// task inspection only when this category opens, without writing runtime state.
async function loadApprovals() {
  approvalRequest?.abort()
  const request = (approvalRequest = new AbortController())
  approvalLoading.value = true
  approvalError.value = ''
  try {
    const result = await api.getTaskInspection(props.turn.id, request.signal)
    if (!request.signal.aborted) {
      history.value = result.approvals || []
      approvalPartial.value = result.projection.truncated || result.approvals === undefined
    }
  } catch (reason) {
    if (!request.signal.aborted)
      approvalError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (approvalRequest === request) approvalLoading.value = false
  }
}
const awaitItems = computed(() => {
  const items = [...props.turn.awaits]
  const ids = new Set(items.map((item) => item.id))
  for (const record of records.value.values()) {
    if (ids.has(record.id)) continue
    items.push({
      id: record.id,
      sessionId: props.turn.sessionId,
      turnId: props.turn.id,
      mode: 'approval',
      title: t('批准 {0}', [record.tool_name]),
      detail: '',
      risk: record.risk_level,
      version: record.version,
    } satisfies AwaitItem)
  }
  return items
})
onMounted(() => {
  if (props.category === 'approvals') void loadApprovals()
})
onScopeDispose(() => approvalRequest?.abort())
const commands = computed(() => commandSteps(props.turn))
const failures = computed(() => props.turn.steps.filter((step) => step.status === 'failed'))
const folderAvailable = computed(
  () => props.folderId !== null && workspace.folders.some((folder) => folder.id === props.folderId),
)
function toggle(id: string, event: Event) {
  if (!(event.currentTarget instanceof HTMLDetailsElement)) return
  if (event.currentTarget.open) opened.value.add(id)
  else opened.value.delete(id)
}
function openFile(path: string | null) {
  if (
    !path ||
    props.folderId === null ||
    !workspace.folders.some((folder) => folder.id === props.folderId)
  )
    return
  workspace.select(props.folderId)
  ui.openFilePreview(path, props.folderId)
}
</script>
<template>
  <div class="receipt-details">
    <template v-if="category === 'files'">
      <ul
        v-if="files.length"
        class="receipt-file-list"
      >
        <li
          v-for="file in files"
          :key="file.path"
        >
          <button
            type="button"
            :disabled="!file.relative || !folderAvailable"
            :title="
              !folderAvailable
                ? $t('工作区已不可用，无法预览。')
                : file.relative
                  ? $t('查看文件内容')
                  : $t('文件不在此工作区内，无法预览')
            "
            @click="openFile(file.relative)"
          >
            <IconSymbol
              name="file"
              :size="14"
            />
            <span>{{ file.path }}</span>
            <IconSymbol
              name="chevron"
              :size="12"
            />
          </button>
        </li>
      </ul>
      <p v-else>
        {{ $t('这条记录未保存文件明细。') }}
      </p>
    </template>
    <template v-else-if="category === 'commands'">
      <details
        v-for="step in commands"
        :key="step.id"
        class="receipt-entry"
        @toggle="toggle(step.id, $event)"
      >
        <summary>
          <IconSymbol
            name="terminal"
            :size="14"
          />
          <code>{{ commandText(step) || $t('命令内容未保存在这条记录中') }}</code>
          <span
            v-if="step.status === 'failed'"
            class="failed-fact"
          >
            {{ $t('失败') }}
          </span>
          <IconSymbol
            name="chevron"
            :size="12"
            class="entry-chevron"
          />
        </summary>
        <template v-if="opened.has(step.id)">
          <RailNodeDetail :steps="[step]" />
          <p v-if="!step.output">
            {{ $t('这条命令没有输出。') }}
          </p>
        </template>
      </details>
      <p v-if="!commands.length">
        {{ $t('这条记录未保存命令明细。') }}
      </p>
    </template>
    <template v-else-if="category === 'approvals'">
      <InkLoader v-if="approvalLoading" />
      <p
        v-if="approvalError"
        role="alert"
      >
        {{ approvalError }}
        <button
          type="button"
          class="text-button"
          :disabled="approvalLoading"
          @click="loadApprovals"
        >
          {{ $t('重试') }}
        </button>
      </p>
      <ReceiptApproval
        v-for="item in awaitItems"
        :key="item.id"
        :item="item"
        :record="records.get(item.id)"
      />
      <p v-if="approvalPartial">
        {{ $t('部分历史明细未完整返回，已显示可读取的记录。') }}
      </p>
      <p v-if="!awaitItems.length && !approvalLoading && !approvalError">
        {{ $t('这条记录未保存审批明细。') }}
      </p>
    </template>
    <template v-else>
      <details
        v-for="step in failures"
        :key="step.id"
        class="receipt-entry"
        @toggle="toggle(step.id, $event)"
      >
        <summary>
          <IconSymbol
            name="alert"
            :size="14"
          />
          <span>{{ commandText(step) || step.title }}</span>
          <IconSymbol
            name="chevron"
            :size="12"
            class="entry-chevron"
          />
        </summary>
        <RailNodeDetail
          v-if="opened.has(step.id)"
          :steps="[step]"
        />
      </details>
      <p
        v-if="turn.error"
        class="failed-fact"
      >
        {{ turn.error }}
      </p>
      <p v-if="!failures.length && !turn.error">
        {{ $t('这条记录未保存失败明细。') }}
      </p>
    </template>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/receipt-details';
</style>
