<script setup lang="ts">
import { computed, ref } from 'vue'
import { i18n } from '@/i18n'
import { useRoute, useRouter } from 'vue-router'
import type { Conversation } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useWorkspaceStore } from '@/stores/workspace'
import { useRunStore } from '@/stores/run'
import { useInboxStore } from '@/stores/inbox'
import { useDialog } from '@/composables/useDialog'
import Pulse from '@/components/shell/Pulse.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import { pendingOrder } from '@/utils/rail-presentation'
import { vTooltip } from '@/utils/tooltip'
import ConversationAuthorizations from './ConversationAuthorizations.vue'
import InkSelect from '@/components/common/InkSelect.vue'
const props = defineProps<{ item: Conversation; collapsed: boolean }>()
const emit = defineEmits<{ select: [] }>()
const session = useSessionStore(),
  workspace = useWorkspaceStore(),
  run = useRunStore(),
  router = useRouter()
const open = ref(false),
  dialog = ref<HTMLDialogElement | null>(null),
  confirming = ref(false)
const title = ref(''),
  folder = ref(''),
  error = ref(''),
  busy = ref<'save' | 'archive' | 'delete' | ''>('')
const inbox = useInboxStore(),
  route = useRoute()
const pending = computed(() =>
  props.item.id === String(route.params.id || '')
    ? []
    : inbox.items
        .filter(
          (item) =>
            item.sessionId === props.item.id &&
            !item.closed &&
            !item.verdict &&
            item.answer === undefined,
        )
        .sort(pendingOrder),
)
const destination = computed(() => ({
  path: '/chat/' + props.item.id,
  ...(pending.value[0] ? { query: { await: pending.value[0].id } } : {}),
}))
const running = computed(
  () =>
    Boolean(session.runningTaskIds[props.item.id]?.length) ||
    (run.turns[props.item.id] || []).some((turn) => ['running', 'waiting'].includes(turn.status)),
)
useDialog(dialog, () => open.value)
const updatedTime = computed(() => {
  const date = new Date(props.item.updated_at)
  if (!Number.isFinite(date.getTime())) return ''
  const today = date.toDateString() === new Date().toDateString()
  return new Intl.DateTimeFormat(
    i18n.global.locale.value,
    today ? { hour: '2-digit', minute: '2-digit' } : { month: 'short', day: 'numeric' },
  ).format(date)
})
function manage() {
  title.value = props.item.title
  folder.value = props.item.folder_id || ''
  confirming.value = false
  error.value = ''
  open.value = true
}
async function act(action: 'save' | 'archive' | 'delete') {
  if (busy.value || (running.value && action !== 'save')) return
  busy.value = action
  error.value = ''
  try {
    if (action === 'delete') await session.deleteSession(props.item.id)
    else
      await session.manageSession(
        props.item.id,
        action === 'archive'
          ? { archived: !props.item.archived }
          : {
              title: title.value.trim(),
              ...(folder.value !== (props.item.folder_id || '') ? { folder_id: folder.value } : {}),
            },
      )
    if (
      (action === 'delete' || action === 'archive') &&
      router.currentRoute.value.params.id === props.item.id
    )
      await router.push('/chat')
    open.value = false
  } catch (reason) {
    if (!open.value) manage()
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = ''
  }
}
</script>
<template>
  <div
    class="session-row"
    :class="{ collapsed }"
    @contextmenu.prevent="manage"
  >
    <router-link
      :to="destination"
      class="session-link"
      :title="item.title"
      @click="emit('select')"
      @dblclick.prevent="manage"
    >
      <Pulse
        v-if="running && !pending.length"
        :state="run.pulse(item.id)"
      />
      <span v-if="!collapsed">{{ item.title || $t('未命名对话') }}</span>
      <small
        v-if="pending.length"
        class="session-pending"
        :title="$t('{0} 项等你处理', [pending.length])"
      >
        <Pulse
          state="waiting"
          :size="10"
        />
        <span v-if="!collapsed">{{ $t('等你处理') }}</span>
      </small>
    </router-link>
    <template v-if="!collapsed">
      <time
        class="session-time"
        :datetime="item.updated_at"
        :title="new Date(item.updated_at).toLocaleString($i18n.locale)"
      >
        {{ updatedTime }}
      </time>
      <div class="session-actions">
        <button
          class="icon-button"
          :disabled="!!busy || running"
          :aria-busy="busy === 'archive' || undefined"
          :aria-label="item.archived ? $t('恢复对话') : $t('归档对话')"
          :title="running ? $t('任务尚未结束') : item.archived ? $t('恢复对话') : $t('归档对话')"
          @click="act('archive')"
        >
          <IconSymbol
            name="archive"
            :size="15"
          />
        </button>
        <button
          v-tooltip="$t('管理对话')"
          class="icon-button"
          :aria-label="$t('管理对话：') + item.title"
          @click="manage"
        >
          <IconSymbol
            name="more"
            :size="16"
          />
        </button>
      </div>
    </template>
    <Teleport to="body">
      <dialog
        ref="dialog"
        class="session-dialog"
        :aria-label="$t('管理对话')"
        @cancel.prevent="open = false"
        @click.self="open = false"
      >
        <header>
          <strong>{{ confirming ? $t('删除对话？') : $t('管理对话') }}</strong>
          <button
            class="icon-button"
            :aria-label="$t('关闭')"
            @click="open = false"
          >
            ×
          </button>
        </header>
        <template v-if="confirming">
          <p>
            {{ $t('将删除「{0}」的消息和执行记录，无法恢复。工作区文件不会删除。', [item.title]) }}
          </p>
          <footer>
            <button
              class="secondary-button"
              :disabled="!!busy"
              @click="confirming = false"
            >
              {{ $t('取消') }}
            </button>
            <button
              class="danger-button"
              :disabled="!!busy || running"
              :aria-busy="busy === 'delete' || undefined"
              @click="act('delete')"
            >
              {{ busy === 'delete' ? $t('正在删除…') : $t('确认删除') }}
            </button>
          </footer>
        </template>
        <template v-else>
          <form @submit.prevent="act('save')">
            <label>
              {{ $t('对话名称') }}
              <input
                v-model="title"
                maxlength="200"
                required
                :disabled="!!busy"
              />
            </label>
            <label>
              {{ $t('所属工作区') }}
              <InkSelect
                v-model="folder"
                :options="
                  workspace.folders.map((entry) => ({ value: entry.id, label: entry.name }))
                "
                :disabled="!!busy || running"
              />
            </label>
            <p
              v-if="running"
              class="empty-copy"
            >
              {{ $t('任务尚未结束，暂时无法移动、归档或删除对话。') }}
            </p>
            <footer>
              <button
                class="primary-button"
                :disabled="!!busy || !title.trim()"
                :aria-busy="busy === 'save' || undefined"
              >
                {{ busy === 'save' ? $t('正在保存…') : $t('保存修改') }}
              </button>
            </footer>
          </form>
          <div class="management-actions">
            <button
              class="secondary-button"
              :disabled="!!busy || running"
              :aria-busy="busy === 'archive' || undefined"
              @click="act('archive')"
            >
              {{ item.archived ? $t('恢复对话') : $t('归档对话') }}
            </button>
            <button
              class="danger-button"
              :disabled="!!busy || running"
              @click="confirming = true"
            >
              {{ $t('删除对话') }}
            </button>
          </div>
          <ConversationAuthorizations
            v-if="open && session.capabilities.approval_authorization_revocation"
            :conversation-id="item.id"
          />
        </template>
        <p
          v-if="error"
          class="error-banner"
          role="alert"
        >
          {{ error }}
        </p>
      </dialog>
    </Teleport>
  </div>
</template>
<style scoped lang="scss">
@use '@/styles/session-item';
</style>
