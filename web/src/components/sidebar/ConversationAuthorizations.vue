<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { api, type ApprovalAuthorization } from '@/api/client'
import { approvalScopeDescription, approvalScopeLabel } from '@/utils/tool-presentation'
import IconSymbol from '@/components/common/IconSymbol.vue'
import Tooltip from '@/components/common/Tooltip.vue'

const props = defineProps<{ conversationId: string }>()
const items = ref<ApprovalAuthorization[]>([])
const loading = ref(false),
  revoking = ref(''),
  error = ref('')
let controller: AbortController | undefined
let generation = 0
let disposed = false
async function refresh() {
  const current = ++generation
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await api.listApprovalAuthorizations(props.conversationId, controller.signal)
    if (current === generation) items.value = result
  } catch (reason) {
    if (current === generation)
      error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (current === generation) loading.value = false
  }
}
async function revoke(item: ApprovalAuthorization) {
  if (revoking.value) return
  revoking.value = item.approval_id
  const currentId = props.conversationId
  let failure = ''
  try {
    await api.revokeApprovalAuthorization(item.approval_id)
  } catch (reason) {
    failure = reason instanceof Error ? reason.message : String(reason)
  }
  if (!disposed && currentId === props.conversationId) {
    // Also reconcile an ambiguous response; do not automatically repeat DELETE.
    await refresh()
    if (failure) error.value = failure
  }
  revoking.value = ''
}
watch(
  () => props.conversationId,
  () => {
    items.value = []
    void refresh()
  },
  { immediate: true },
)
onScopeDispose(() => {
  disposed = true
  generation++
  controller?.abort()
})
</script>
<template>
  <section
    class="conversation-authorizations"
    :aria-label="$t('已允许的操作')"
  >
    <header>
      <strong>{{ $t('已允许的操作') }}</strong>
      <div class="authorization-actions">
        <Tooltip :content="$t('撤销后不再使用这次批准；正在执行的操作不受影响。')">
          <button
            type="button"
            class="icon-button"
            :aria-label="$t('撤销后不再使用这次批准；正在执行的操作不受影响。')"
          >
            <IconSymbol
              name="info"
              :size="14"
            />
          </button>
        </Tooltip>
        <button
          type="button"
          class="text-button"
          :disabled="loading || !!revoking"
          @click="refresh"
        >
          {{ $t('刷新') }}
        </button>
      </div>
    </header>
    <p
      v-if="loading"
      role="status"
    >
      {{ $t('正在读取…') }}
    </p>
    <p
      v-else-if="!items.length && !error"
      class="empty-copy"
    >
      {{ $t('当前没有已保存的批准。') }}
    </p>
    <article
      v-for="item in items"
      :key="item.approval_id"
    >
      <details>
        <summary>{{ approvalScopeLabel(item.scope, item.resource) }}</summary>
        <p
          v-for="line in approvalScopeDescription(item.resource, item.scope)"
          :key="line"
        >
          {{ line }}
        </p>
      </details>
      <p>{{ $t('有效至：{0}', [new Date(item.expires_at).toLocaleString($i18n.locale)]) }}</p>
      <button
        type="button"
        class="secondary-button"
        :disabled="!!revoking || loading"
        @click="revoke(item)"
      >
        {{ revoking === item.approval_id ? $t('正在撤销…') : $t('撤销批准') }}
      </button>
    </article>
    <p
      v-if="error"
      class="error-banner"
      role="alert"
    >
      {{ error }}
    </p>
  </section>
</template>
<style scoped>
.conversation-authorizations {
  margin-block: var(--space-4);
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
}
.authorization-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.authorization-actions :deep(.tooltip) {
  inset-inline-start: auto;
  inset-inline-end: 0;
  max-width: var(--measure-240px);
}
article {
  padding-block: var(--space-3);
  border-top: var(--hairline) solid var(--rule);
}
p {
  font-size: var(--text-xs);
  overflow-wrap: anywhere;
}
</style>
