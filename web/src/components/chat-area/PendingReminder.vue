<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AwaitItem } from '@/api/events'
const props = defineProps<{ items: AwaitItem[]; scrollRoot: HTMLElement | null }>()
const emit = defineEmits<{ locate: [item: AwaitItem] }>()
const visible = ref<Set<string>>(new Set())
const hidden = computed(() => props.items.filter((item) => !visible.value.has(item.id)))
let intersection: IntersectionObserver | undefined,
  mutation: MutationObserver | undefined,
  frame = 0
let observedRoot: HTMLElement | null = null
const targets = new Map<string, HTMLElement>()
function observe() {
  if (!props.items.length || !props.scrollRoot || typeof IntersectionObserver === 'undefined')
    return
  if (observedRoot !== props.scrollRoot) {
    intersection?.disconnect()
    targets.clear()
    visible.value = new Set()
    observedRoot = props.scrollRoot
    intersection = new IntersectionObserver(
      (entries) => {
        const next = new Set(visible.value)
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).closest<HTMLElement>('[data-await]')?.dataset
            .await
          if (id && targets.get(id) === entry.target) {
            if (entry.isIntersecting) next.add(id)
            else next.delete(id)
          }
        }
        visible.value = next
      },
      { root: props.scrollRoot, threshold: 0.5 },
    )
  }
  const next = new Map<string, HTMLElement>()
  for (const item of props.items) {
    const card = props.scrollRoot.querySelector<HTMLElement>(
      `[data-await="${CSS.escape(item.id)}"]`,
    )
    const target = card?.querySelector<HTMLElement>('[data-await-action]:not(:disabled)') || card
    if (target) next.set(item.id, target)
  }
  // Streaming text mutations must not reset visibility or rebuild observers.
  for (const [id, target] of targets) {
    if (next.get(id) === target) continue
    intersection?.unobserve(target)
    targets.delete(id)
    if (visible.value.has(id)) {
      const remaining = new Set(visible.value)
      remaining.delete(id)
      visible.value = remaining
    }
  }
  for (const [id, target] of next) {
    if (targets.get(id) === target) continue
    targets.set(id, target)
    intersection?.observe(target)
  }
}
function schedule() {
  if (!frame)
    frame = requestAnimationFrame(() => {
      frame = 0
      observe()
    })
}
watch(
  [() => props.scrollRoot, () => props.items.map((item) => item.id).join('|')],
  ([root, ids]) => {
    mutation?.disconnect()
    if (!root || !ids) {
      intersection?.disconnect()
      observedRoot = null
      targets.clear()
      visible.value = new Set()
      if (frame) cancelAnimationFrame(frame)
      frame = 0
      return
    }
    if (typeof MutationObserver !== 'undefined') {
      // Ordinary Markdown replacements cannot add or remove approval controls.
      const selector = '[data-await], [data-await-action]'
      mutation = new MutationObserver((records) => {
        if (
          records.some((record) => {
            if (
              record.target instanceof Element &&
              record.target.closest('.markdown-content, .code-block')
            )
              return false
            return (
              (record.type === 'attributes' &&
                record.target instanceof Element &&
                Boolean(record.target.closest('[data-await]'))) ||
              [...record.addedNodes, ...record.removedNodes].some(
                (node) =>
                  node instanceof Element &&
                  (node.matches(selector) || Boolean(node.querySelector(selector))),
              )
            )
          })
        )
          schedule()
      })
      mutation.observe(root, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['disabled'],
      })
    }
    schedule()
  },
  { immediate: true, flush: 'post' },
)
onMounted(schedule)
onBeforeUnmount(() => {
  intersection?.disconnect()
  mutation?.disconnect()
  cancelAnimationFrame(frame)
})
</script>
<template>
  <button
    v-if="hidden.length"
    class="pending-reminder"
    @click="emit('locate', hidden[0])"
  >
    <svg
      viewBox="0 0 16 16"
      aria-hidden="true"
    >
      <path d="M8 2l6 6-6 6-6-6Z" />
    </svg>
    {{ $t('{0} 项等你处理', [hidden.length]) }}
    <span aria-hidden="true">↓</span>
  </button>
</template>
<style scoped lang="scss">
.pending-reminder {
  align-self: center;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: none;
  max-width: calc(100% - var(--space-8));
  margin: var(--space-3) auto;
  padding: var(--space-3) var(--space-5);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-pill);
  background: var(--paper-raised);
  color: var(--wait-ink);
  box-shadow: var(--shadow-raised);
  font-size: var(--text-sm);
  animation: inkline-reduced-fade var(--motion-fast) var(--ease-out);
}
svg {
  width: var(--space-5);
  height: var(--space-5);
  fill: currentColor;
}
</style>
