<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/i18n'
import type { UserMemory } from '@/api/client'
import { useMemoryStore } from '@/stores/memory'
import { useSessionStore } from '@/stores/session'
import { confidencePercent } from '@/utils/work-records'
import IconSymbol from '@/components/common/IconSymbol.vue'
import { confirmAction } from '@/utils/confirm-action'
const props = defineProps<{ item: UserMemory; focused?: boolean; editing?: boolean }>()
const emit = defineEmits<{ (event: 'edit', item: UserMemory): void }>()
const memory = useMemoryStore(),
  session = useSessionStore()
const confidence = computed(() => confidencePercent(props.item.confidence))
const categories: Record<UserMemory['category'], string> = {
  preference: '偏好',
  constraint: '约束',
  fact: '事实',
  project: '项目',
  other: '其他',
}
async function remove(item: UserMemory) {
  if (memory.saving) return
  if (await confirmAction(t('遗忘这条记忆？')) && !memory.saving && props.item.id === item.id)
    await memory.remove(item.id)
}
</script>
<template>
  <article
    :id="'memory-' + item.id"
    class="memory-item"
    :class="{ 'focused-memory': focused }"
    tabindex="-1"
  >
    <p>{{ item.content.replace(/^人设：/, '') }}</p>
    <div class="memory-metadata">
      <small>{{ $t(categories[item.category]) }}</small>
      <span
        class="memory-confidence"
        role="meter"
        :aria-label="$t('置信度')"
        :aria-valuemin="0"
        :aria-valuemax="100"
        :aria-valuenow="confidence"
        :aria-valuetext="$t('置信度 {0}%', [confidence])"
      ><i :style="{ '--memory-confidence': confidence / 100 }" /><small>{{ confidence }}%</small></span>
      <router-link
        v-if="session.sessions.some((record) => record.id === item.source_id)"
        :to="'/chat/' + item.source_id"
      >
        {{ $t('来源会话') }}
      </router-link>
      <small v-else>{{ item.source_type === 'user' ? $t('由你写下') : $t('助手记下') }}</small>
    </div>
    <slot />
    <footer>
      <button
        :aria-pressed="memory.pinnedIds.includes(item.id)"
        :disabled="memory.saving"
        @click="memory.togglePinned(item.id)"
      >
        <IconSymbol
          name="target"
          :size="14"
        />{{
          memory.pinnedIds.includes(item.id) ? $t('已置顶') : $t('置顶')
        }}
      </button>
      <button
        v-if="!editing"
        :disabled="memory.saving"
        @click="emit('edit', item)"
      >
        <IconSymbol
          name="compose"
          :size="14"
        />{{ $t('编辑') }}
      </button>
      <button
        class="danger"
        :disabled="memory.saving"
        @click="remove(item)"
      >
        {{ $t('遗忘') }}
      </button>
    </footer>
  </article>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.memory-item {
  position: relative;
  isolation: isolate;
  padding: var(--space-5) 0;
  border-bottom: var(--hairline) solid var(--rule);
}
.memory-item::before {
  content: '';
  position: absolute;
  inset: 0 calc(-1 * var(--space-3));
  z-index: var(--z-underlay);
  background: var(--memory-focus-wash);
  opacity: 0;
  pointer-events: none;
}
.focused-memory::before {
  animation: memory-highlight var(--memory-focus-duration) var(--ease-out) both;
}
p {
  margin: 0 0 var(--space-4);
  font-size: var(--type-body);
  line-height: var(--leading-reading);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.memory-metadata {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
}
small,
a {
  color: var(--ink-2);
  font-size: var(--type-caption);
}
.memory-confidence {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-variant-numeric: tabular-nums;
}
.memory-confidence > i {
  display: block;
  position: relative;
  width: var(--memory-meter-width);
  height: var(--memory-meter-height);
  background: var(--rule);
}
.memory-confidence > i::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--ink-2);
  transform: scaleX(var(--memory-confidence));
  transform-origin: left;
}
footer {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-top: var(--space-4);
}
button {
  @include mixins.interactive-row;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  font-size: var(--type-caption);
  color: var(--ink-2);
  background: var(--transparent);
}
button.danger {
  color: var(--seal);
}
button[aria-pressed='true'] {
  @include mixins.text-ink;
}
@keyframes memory-highlight {
  from {
    opacity: 1;
  }
  to {
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .focused-memory::before {
    animation: paper-fade var(--motion-reduced) var(--ease-out) reverse both !important;
  }
}
</style>
