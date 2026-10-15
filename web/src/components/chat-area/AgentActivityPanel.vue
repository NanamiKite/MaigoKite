<script setup lang="ts">
import { t } from '@/i18n'
import { computed, ref, watch } from 'vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
import type { AgentActivityGroup, AgentActivityItem } from '@/api/client'

const props = defineProps<{ group: AgentActivityGroup }>()
const hasAttention = computed(() =>
  props.group.items.some(
    (item) => item.status === 'failed' || item.status === 'denied' || item.hint,
  ),
)
const expanded = ref(props.group.status === 'running' || hasAttention.value)

const isRunning = computed(
  () =>
    props.group.status === 'running' ||
    props.group.items.some((item) => item.status === 'pending' || item.status === 'running'),
)
const heading = computed(() => {
  if (props.group.status === 'failed') return t('执行失败')
  if (props.group.status === 'cancelled') return t('已停止')
  if (isRunning.value) return t('正在工作')
  const failed = props.group.items.filter(
    (item) => item.status === 'failed' || item.status === 'denied',
  ).length
  if (failed) return t('已完成，{0} 项需要处理', [failed])
  return t('已完成 {0} 项操作', [props.group.items.length])
})

watch(isRunning, (running) => {
  if (running) expanded.value = true
})
watch(hasAttention, (attention) => {
  if (attention) expanded.value = true
})

function itemIcon(item: AgentActivityItem): string {
  if (item.status === 'failed' || item.status === 'denied') return 'alert'
  if (item.kind === 'reasoning') return 'brain'
  if (item.kind === 'subagent') return 'cube'
  return item.status === 'completed' ? 'check' : 'terminal'
}

function statusLabel(item: AgentActivityItem): string {
  return {
    pending: t('等待执行'),
    running: t('进行中'),
    completed: t('完成'),
    failed: t('失败'),
    denied: t('已拦截'),
  }[item.status]
}

function detailText(item: AgentActivityItem): string {
  if (item.detail === undefined || item.detail === null) return ''
  const value = typeof item.detail === 'string' ? item.detail : JSON.stringify(item.detail, null, 2)
  return value === '{}' ? '' : value.slice(0, 20000)
}
</script>

<template>
  <section
    class="activity-panel"
    :class="group.status"
    data-testid="agent-activity"
  >
    <button
      type="button"
      class="activity-heading"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span
        class="activity-state"
        :class="{ pulse: isRunning }"
      >
        <IconSymbol
          :name="isRunning ? 'activity' : hasAttention ? 'alert' : 'check'"
          :size="14"
        />
      </span>
      <strong>{{ heading }}</strong>
      <small v-if="group.items.length">{{ $t('{0} 条记录', [group.items.length]) }}</small>
      <IconSymbol
        name="chevron"
        :size="13"
        class="disclosure"
        :class="{ open: expanded }"
      />
    </button>

    <div
      v-show="expanded"
      class="activity-items"
    >
      <p
        v-if="!group.items.length"
        class="waiting-row"
      >
        <span />
        {{ $t('正在处理…') }}
      </p>
      <article
        v-for="item in group.items"
        :key="item.id"
        class="activity-item"
        :class="[item.kind, item.status]"
        :data-activity-kind="item.kind"
      >
        <span class="item-rail">
          <i />
          <span />
        </span>
        <span class="item-icon">
          <IconSymbol
            :name="itemIcon(item)"
            :size="14"
          />
        </span>
        <div class="item-copy">
          <div class="item-title">
            <strong>{{ item.kind === 'reasoning' ? $t('思考') : item.title }}</strong>
            <small>{{ statusLabel(item) }}</small>
          </div>
          <p v-if="item.summary">
            {{ item.summary }}
          </p>
          <aside
            v-if="item.hint"
            class="item-hint"
            role="status"
          >
            <IconSymbol
              name="info"
              :size="14"
            />
            <span>{{ item.hint }}</span>
            <router-link
              v-if="item.action"
              :to="item.action.to"
            >
              {{ item.action.label }}
            </router-link>
          </aside>
          <details
            v-if="detailText(item)"
            class="item-detail"
          >
            <summary>{{ $t('查看详情') }}</summary>
            <pre>{{ detailText(item) }}</pre>
          </details>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped lang="scss">
@use '@/styles/agent-activity';
</style>
