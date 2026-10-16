<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useInboxStore } from '@/stores/inbox'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { pendingItem } from '@/utils/rail-presentation'
import ApprovalCard from '@/components/chat-area/ApprovalCard.vue'
import QuestionCard from '@/components/interact/QuestionCard.vue'
import InkLoader from '@/components/common/InkLoader.vue'
import IconSymbol from '@/components/common/IconSymbol.vue'
const inbox = useInboxStore(),
  session = useSessionStore(),
  ui = useUIStore(),
  route = useRoute()
const loading = ref(false)
const items = computed(() => inbox.items.filter(pendingItem))
const currentId = computed(() => (route.name === 'Chat' ? String(route.params.id || '') : ''))
async function refresh() {
  loading.value = true
  try {
    await inbox.refresh()
  } finally {
    loading.value = false
  }
}
onMounted(() => {
  void refresh()
})
</script>
<template>
  <section
    class="pending-tab"
    :aria-label="$t('等你处理')"
  >
    <header class="pending-heading">
      <span>{{ $t('等你处理') }}</span>
      <small>{{ items.length }}</small>
      <button
        class="icon-button"
        :disabled="loading"
        :aria-label="$t('重新读取')"
        @click="refresh"
      >
        <IconSymbol
          name="refresh"
          :size="16"
        />
      </button>
    </header>
    <InkLoader v-if="loading && !items.length" />
    <div
      v-else-if="!items.length && !inbox.error"
      class="pending-empty"
    >
      <svg
        viewBox="0 0 120 100"
        aria-hidden="true"
      >
        <path
          class="empty-line"
          d="M60 5C48 25 70 42 60 56M60 76v19"
        />
        <path
          class="empty-diamond"
          d="m60 46 15 15-15 15-15-15Z"
        />
        <path
          class="empty-tick"
          d="m54 61 4 4 8-9"
        />
      </svg>
      <h3>{{ $t('都处理好了') }}</h3>
      <p>{{ $t('有操作需要确认，或问题需要回答时，会出现在这里。') }}</p>
    </div>
    <article
      v-for="item in items"
      :key="item.id"
      class="pending-entry"
    >
      <header class="pending-session">
        <router-link
          :to="{ path: '/chat/' + item.sessionId, query: { await: item.id } }"
          @click="ui.drawerOpen = false"
        >
          {{
            session.sessions.find((entry) => entry.id === item.sessionId)?.title || $t('未命名对话')
          }}
          <IconSymbol
            name="chevron"
            :size="12"
          />
        </router-link>
        <small v-if="item.sessionId === currentId">{{ $t('当前对话') }}</small>
      </header>
      <ApprovalCard
        v-if="item.mode === 'approval'"
        :item="item"
      />
      <QuestionCard
        v-else
        :item="item"
      />
    </article>
    <p
      v-if="inbox.error"
      class="pending-error"
      role="alert"
    >
      {{ inbox.error }}
      <button
        class="secondary-button"
        :disabled="loading"
        @click="refresh"
      >
        {{ $t('重新读取') }}
      </button>
    </p>
  </section>
</template>
<style scoped lang="scss">
.pending-tab {
  padding: var(--space-5);
}
.pending-heading,
.pending-session {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.pending-heading {
  margin-bottom: var(--space-5);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}
.pending-heading .icon-button {
  margin-left: auto;
}
small {
  font-size: var(--text-xs);
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
.pending-entry {
  padding-bottom: var(--space-5);
  margin-bottom: var(--space-5);
  border-bottom: var(--hairline) solid var(--rule);
}
.pending-session a {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-serif);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}
.pending-session small {
  margin-left: auto;
  white-space: nowrap;
}
.pending-session .icon-symbol {
  color: var(--accent);
}
.pending-empty {
  padding: var(--space-8) var(--space-5);
  text-align: center;
}
.pending-empty svg {
  width: var(--space-10);
  height: var(--space-10);
  fill: none;
  stroke-linecap: round;
  stroke-width: var(--rail-stroke);
}
.empty-line {
  stroke: var(--rule);
}
.empty-diamond {
  stroke: var(--wait);
}
.empty-tick {
  stroke: var(--ink-2);
}
.pending-empty h3 {
  font-size: var(--type-subheading);
  font-weight: var(--weight-medium);
}
.pending-empty p,
.pending-error {
  color: var(--ink-2);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}
</style>
