<script setup lang="ts">
import { computed } from 'vue'
import type { ChatModelProfile } from '@/api/client'
import type { ModelServiceGroup } from '@/config/model-groups'
import IconSymbol from '@/components/common/IconSymbol.vue'

const props = defineProps<{
  group: ModelServiceGroup<ChatModelProfile>
  expanded: boolean
  saving: boolean
  profileCredential: (profile: ChatModelProfile) => { label: string; tone: string }
}>()
const emit = defineEmits<{
  expanded: [value: boolean]
  edit: [id: string]
  default: [id: string]
}>()
const listId = `service-models-${Array.from(crypto.getRandomValues(new Uint32Array(2)), (value) => value.toString(16)).join('-')}`
const rows = computed(() =>
  props.group.models.map((item) => ({ item, credential: props.profileCredential(item) })),
)
function toggle(event: Event) {
  if (
    event.currentTarget instanceof HTMLDetailsElement &&
    event.currentTarget.open !== props.expanded
  )
    emit('expanded', event.currentTarget.open)
}
</script>

<template>
  <details
    class="service-group"
    :open="expanded"
    @toggle="toggle"
  >
    <summary
      class="service-heading"
      :aria-label="$t('展开或收起 {0} 的模型', [group.name])"
      :aria-controls="listId"
    >
      <IconSymbol
        class="service-chevron"
        name="chevron"
        :size="14"
      />
      <span class="profile-main">
        <span class="provider-mark">{{ group.monogram }}</span>
        <span class="profile-copy">
          <strong>{{ group.name }}</strong>
          <small>
            {{ $t(group.providerName) }}
            <template v-if="group.location">· {{ group.location }}</template>
          </small>
        </span>
      </span>
      <span
        v-if="group.models.some((item) => item.isDefault)"
        class="service-default"
      >
        {{ $t('默认') }}
      </span>
      <span class="service-count">{{ $t('{0} 个模型', [group.models.length]) }}</span>
      <button
        type="button"
        class="default-action"
        :disabled="saving"
        :aria-label="$t('管理 {0} 的模型', [group.name])"
        @click.stop.prevent="emit('edit', group.models[0].id)"
      >
        {{ $t('管理模型 ›') }}
      </button>
    </summary>
    <ul
      :id="listId"
      class="service-models"
      :aria-label="$t('{0} 的模型', [group.name])"
    >
      <li
        v-for="{ item, credential } in rows"
        :key="item.id"
        class="profile-row"
      >
        <span class="profile-copy">
          <span>
            <strong>{{ item.model }}</strong>
            <i v-if="item.isDefault">{{ $t('默认') }}</i>
          </span>
        </span>
        <span
          class="credential-state"
          :class="credential.tone"
        >
          <i />
          {{ credential.label }}
        </span>
        <button
          v-if="!item.isDefault"
          type="button"
          class="default-action"
          :disabled="saving"
          :aria-label="$t('将 {0} 设为默认', [item.model])"
          @click="emit('default', item.id)"
        >
          {{ $t('设为默认') }}
        </button>
      </li>
    </ul>
  </details>
</template>

<style scoped lang="scss">
@use '@/styles/model-service-card';
</style>
