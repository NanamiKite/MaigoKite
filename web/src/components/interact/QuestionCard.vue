<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { t } from '@/i18n'
import type { AwaitItem } from '@/api/events'
import { useInboxStore } from '@/stores/inbox'
import InkButton from '@/components/common/InkButton.vue'
import { awaitFocus } from '@/utils/await-focus'
const card = ref<HTMLElement | null>(null)
const props = defineProps<{ item: AwaitItem }>()
const inbox = useInboxStore()
const answer = ref(''),
  failure = ref('')
const caption = computed(() =>
  props.item.title?.trim() ? props.item.title : t('Agent 在等你回答'),
)
async function submit() {
  if (!answer.value.trim() || inbox.busy.includes(props.item.id)) return
  failure.value = ''
  const restoreFocus = awaitFocus(card.value)
  try {
    await inbox.answer(props.item, answer.value)
  } catch (reason) {
    failure.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    await nextTick()
    restoreFocus()
  }
}
</script>
<template>
  <article
    ref="card"
    class="question-card"
    :data-await="item.id"
  >
    <p class="question-caption">
      <span>{{ $t('待回答') }}</span>{{ caption }}
    </p>
    <form @submit.prevent="submit">
      <div
        v-if="item.options?.length"
        class="options"
      >
        <button
          v-for="option in item.options"
          :key="option.label"
          type="button"
          class="secondary-button"
          data-await-action
          :aria-pressed="answer === option.label"
          :disabled="inbox.busy.includes(item.id)"
          @click="answer = option.label"
        >
          {{ option.label }}{{ option.recommended ? $t(' · 推荐') : '' }}
        </button>
      </div>
      <label>{{ $t('你的回答')
      }}<textarea
        v-model="answer"
        rows="3"
        maxlength="10000"
        :disabled="inbox.busy.includes(item.id)"
        :placeholder="$t('选择建议答案，或输入自己的回答')"
      />
      </label>
      <p
        v-if="failure"
        role="alert"
      >
        {{ failure }}
      </p>
      <InkButton
        tone="primary"
        data-await-action
        :loading="inbox.busy.includes(item.id)"
        :disabled="!answer.trim() || inbox.busy.includes(item.id)"
        @click="submit"
      >
        {{ inbox.busy.includes(item.id) ? $t('正在提交…') : $t('回答并继续') }}
      </InkButton>
    </form>
  </article>
</template>
<style scoped lang="scss">
@use '@/styles/mixins' as mixins;
.question-card {
  border-left: var(--space-1) solid var(--wait);
  padding: var(--space-4) var(--space-5);
  color: var(--ink);
  background: var(--paper-2);
  font-size: var(--text-sm);
}
form,
label {
  display: grid;
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.options {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}
textarea {
  width: 100%;
}
button[aria-pressed='true'] {
  @include mixins.text-ink;
}
.question-caption {
  margin: 0 0 var(--space-4);
  font-size: var(--text-sm);
  color: var(--ink-2);
  line-height: var(--leading-normal);
}
.question-caption span {
  display: inline-block;
  margin-right: var(--space-3);
  font-size: var(--text-xs);
  color: var(--wait-ink);
}
</style>
