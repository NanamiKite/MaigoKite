<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import type { AttachmentReference } from '@/api/images'
import { t } from '@/i18n'
const props = defineProps<{
  items: { id: string; content: string; attachments?: AttachmentReference[] }[]
  paused: boolean
  guideReason: string
  canContinue: boolean
  guide: (id: string) => Promise<void>
}>()
const emit = defineEmits<{
  (event: 'edit', id: string, content: string): void
  (event: 'remove', index: number): void
  (event: 'continue'): void
  (event: 'reorder', id: string, target: string, position: 'before' | 'after'): void
}>()
const editing = ref(''),
  draft = ref(''),
  busy = ref(''),
  error = ref('')
const list = ref<HTMLElement | null>(null)
const dragged = ref(''),
  target = ref(''),
  position = ref<'before' | 'after'>('before'),
  announcement = ref('')
const sortable = computed(() => props.items.length > 1 && !busy.value && !editing.value)
const dragType = 'application/x-maigokite-queue'
function endDrag() {
  dragged.value = target.value = ''
}
function startDrag(event: DragEvent, id: string) {
  if (!sortable.value || !event.dataTransfer) {
    event.preventDefault()
    return
  }
  dragged.value = id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData(dragType, id)
  const row = (event.currentTarget as HTMLElement).closest<HTMLElement>('[data-queued-id]')
  if (row) {
    const bounds = row.getBoundingClientRect()
    event.dataTransfer.setDragImage(row, event.clientX - bounds.left, event.clientY - bounds.top)
  }
}
function allowDrop(event: DragEvent) {
  if (!dragged.value || !sortable.value) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}
function dragOver(event: DragEvent, id: string) {
  if (!dragged.value || !sortable.value) return
  allowDrop(event)
  target.value = dragged.value === id ? '' : id
  const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect()
  position.value = event.clientY < bounds.top + bounds.height / 2 ? 'before' : 'after'
}
function dragLeave(event: DragEvent) {
  if (
    !(event.relatedTarget instanceof Node) ||
    !(event.currentTarget as HTMLElement).contains(event.relatedTarget)
  )
    target.value = ''
}
async function move(id: string, destination: string, where: 'before' | 'after', keyboard = false) {
  emit('reorder', id, destination, where)
  await nextTick()
  const index = props.items.findIndex((item) => item.id === id)
  if (index < 0) return
  announcement.value = t('消息已移至第 {0} 项', [index + 1])
  if (keyboard) {
    const handle = list.value?.querySelector<HTMLButtonElement>(
      `[data-queued-id="${CSS.escape(id)}"] .queue-drag-handle`,
    )
    handle?.focus({ preventScroll: true })
    handle?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }
}
function drop(event: DragEvent) {
  if (!dragged.value) return
  event.preventDefault()
  const id = dragged.value,
    destination = target.value,
    where = position.value
  const valid = sortable.value && event.dataTransfer?.getData(dragType) === id
  endDrag()
  if (valid && destination) void move(id, destination, where)
}
function moveKey(event: KeyboardEvent, id: string) {
  if (
    !sortable.value ||
    event.isComposing ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    !['ArrowUp', 'ArrowDown'].includes(event.key)
  )
    return
  event.preventDefault()
  event.stopPropagation()
  const index = props.items.findIndex((item) => item.id === id),
    down = event.key === 'ArrowDown'
  const neighbor = props.items[index + (down ? 1 : -1)]
  if (index >= 0 && neighbor) void move(id, neighbor.id, down ? 'after' : 'before', true)
}
watch([() => props.items.map((item) => item.id), sortable], ([ids, enabled]) => {
  if (!enabled || !ids.includes(dragged.value) || (target.value && !ids.includes(target.value)))
    endDrag()
})
function edit(item: { id: string; content: string }) {
  editing.value = item.id
  draft.value = item.content
}
function save() {
  if (draft.value.trim()) {
    emit('edit', editing.value, draft.value)
    editing.value = ''
  }
}
async function guideNow(id: string) {
  busy.value = id
  error.value = ''
  try {
    await props.guide(id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = ''
  }
}
</script>
<template>
  <section
    v-if="items.length"
    ref="list"
    class="queued-messages"
    :aria-label="$t('消息队列')"
    @dragover="allowDrop"
    @dragleave="dragLeave"
    @drop="drop"
  >
    <header>
      {{ $t('队列（{0}）', [items.length]) }}
      <span v-if="paused">{{ $t('已暂停自动发送') }}</span>
    </header>
    <TransitionGroup
      tag="div"
      name="queue"
      role="list"
    >
      <article
        v-for="(item, index) in items"
        :key="item.id"
        class="queue-item"
        role="listitem"
        :class="{ 'is-dragging': dragged === item.id }"
        :data-queued-id="item.id"
        :data-drop-position="target === item.id ? position : undefined"
        @dragover="dragOver($event, item.id)"
      >
        <button
          v-if="editing !== item.id"
          type="button"
          class="queue-drag-handle"
          :draggable="sortable"
          :disabled="!sortable"
          :aria-label="$t('调整第 {0} 条消息的顺序', [index + 1])"
          :title="$t('拖动排序，或用上下方向键移动')"
          @dragstart="startDrag($event, item.id)"
          @dragend="endDrag"
          @keydown="moveKey($event, item.id)"
        >
          <IconSymbol
            name="grip"
            :size="14"
          />
        </button>
        <template v-if="editing === item.id">
          <textarea
            v-model="draft"
            :aria-label="$t('编辑待发消息')"
            rows="2"
            @keydown.ctrl.enter.prevent="save"
            @keydown.esc.stop="editing = ''"
          />
          <div class="queue-edit-actions">
            <button
              type="button"
              class="text-button"
              :disabled="!draft.trim()"
              @click="save"
            >
              {{ $t('保存') }}
            </button>
            <button
              type="button"
              class="text-button"
              @click="editing = ''"
            >
              {{ $t('取消') }}
            </button>
          </div>
        </template>
        <template v-else>
          <p>{{ item.content }}</p>
          <small v-if="item.attachments?.length">
            {{ $t('包含 {0} 个附件', [item.attachments.length]) }}
          </small>
          <div class="queue-actions">
            <button
              type="button"
              class="text-button"
              :disabled="Boolean(guideReason) || Boolean(busy) || Boolean(item.attachments?.length)"
              :title="
                item.attachments?.length
                  ? $t('请等待当前任务结束后再发送附件。')
                  : guideReason || undefined
              "
              @click="guideNow(item.id)"
            >
              <IconSymbol
                name="arrow-up"
                :size="14"
              />
              {{ $t('立即发送') }}
            </button>
            <button
              type="button"
              class="icon-button"
              :disabled="Boolean(busy)"
              :title="$t('编辑')"
              @click="edit(item)"
            >
              <IconSymbol
                name="edit"
                :size="14"
              />
              <span class="sr-only">{{ $t('编辑') }}</span>
            </button>
            <button
              type="button"
              class="icon-button"
              :disabled="Boolean(busy)"
              :title="$t('移除')"
              @click="emit('remove', index)"
            >
              <IconSymbol
                name="close"
                :size="14"
              />
              <span class="sr-only">{{ $t('移除') }}</span>
            </button>
          </div>
        </template>
      </article>
    </TransitionGroup>
    <span
      class="sr-only"
      role="status"
      aria-live="polite"
    >
      {{ announcement }}
    </span>
    <p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p>
    <button
      v-if="paused"
      class="text-button"
      :disabled="!canContinue"
      @click="emit('continue')"
    >
      {{ $t('确认继续发送') }}
    </button>
  </section>
</template>
<style scoped lang="scss">
@use '@/styles/queued-messages';
</style>
