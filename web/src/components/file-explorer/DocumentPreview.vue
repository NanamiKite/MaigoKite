<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import {
  readDocument,
  navigateDocument,
  renderDocumentPage,
  documentOcrLanguages,
  type DocumentPage,
  type DocumentNavigation,
} from '@/api/images'
import InkLoader from '@/components/common/InkLoader.vue'

const props = defineProps<{ path: string; folderId: string; sha256: string }>()
const mode = ref<'text_layer' | 'render' | 'ocr'>('text_layer')
const imageUrl = ref(''),
  ocrReady = ref(false)
const languageRequest = new AbortController()
onMounted(async () => {
  try {
    const languages = await documentOcrLanguages(languageRequest.signal)
    if (!languageRequest.signal.aborted) ocrReady.value = languages.length > 0
  } catch {
    // A failed capability lookup must not advertise recognition as available.
    ocrReady.value = false
  }
})
const page = ref<DocumentPage | null>(null),
  busy = ref(false),
  error = ref(''),
  target = ref(1)
const query = ref(''),
  navigation = ref<DocumentNavigation | null>(null),
  navigationBusy = ref(false),
  navigationError = ref(''),
  navigationOffset = ref(0)
const unitLabel = computed(
  () =>
    ({ pdf: '页码', docx: '段落或表格', xlsx: '工作表分段', pptx: '幻灯片' })[
      page.value?.format || 'pdf'
    ],
)
function changeMode(value: 'text_layer' | 'render' | 'ocr') {
  void load(page.value?.unit || 1, 0, value)
}
let navigationController: AbortController | undefined
let controller: AbortController | undefined
let contentHash = ''
let requestedUnit = 1,
  requestedOffset = 0
async function load(unit: number, offset = 0, view: typeof mode.value = mode.value) {
  mode.value = view
  controller?.abort()
  URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
  const request = new AbortController()
  controller = request
  requestedUnit = unit
  requestedOffset = offset
  busy.value = true
  error.value = ''
  try {
    const result = await readDocument(
      props.path,
      props.folderId,
      unit,
      offset,
      props.sha256 || contentHash,
      request.signal,
      view === 'ocr' ? 'ocr' : 'text_layer',
    )
    if (controller !== request || request.signal.aborted) return
    page.value = result
    target.value = result.unit
    contentHash = result.sha256
    if (view === 'render') {
      const image = await renderDocumentPage(
        props.path,
        props.folderId,
        unit,
        contentHash,
        request.signal,
      )
      if (controller !== request || request.signal.aborted) return
      imageUrl.value = URL.createObjectURL(image)
    }
  } catch (reason) {
    if (controller === request && !request.signal.aborted)
      error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (controller === request) busy.value = false
  }
}
async function lookup(action: 'outline' | 'search', offset = 0) {
  const searchText = action === 'search' ? query.value.trim() : ''
  if (action === 'search' && !searchText) return
  navigationController?.abort()
  const request = new AbortController()
  navigationController = request
  navigationBusy.value = true
  navigationError.value = ''
  navigation.value = null
  try {
    const result = await navigateDocument(
      props.path,
      props.folderId,
      props.sha256 || contentHash,
      action,
      searchText,
      offset,
      request.signal,
    )
    if (navigationController !== request || request.signal.aborted) return
    navigation.value = result
    navigationOffset.value = offset
  } catch (reason) {
    if (navigationController === request && !request.signal.aborted)
      navigationError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (navigationController === request) navigationBusy.value = false
  }
}
watch(query, () => {
  navigationController?.abort()
  navigationController = undefined
  navigationBusy.value = false
  navigationError.value = ''
  navigation.value = null
})
function jump() {
  if (
    !Number.isInteger(target.value) ||
    target.value < 1 ||
    target.value > (page.value?.total_units || 1)
  )
    return
  void load(target.value)
}
watch(
  () => [props.path, props.folderId, props.sha256],
  () => {
    navigationController?.abort()
    navigationController = undefined
    navigationBusy.value = false
    navigation.value = null
    navigationError.value = ''
    query.value = ''
    page.value = null
    mode.value = 'text_layer'
    contentHash = ''
    target.value = 1
    void load(1)
  },
  { immediate: true },
)
onScopeDispose(() => {
  languageRequest.abort()
  URL.revokeObjectURL(imageUrl.value)
  navigationController?.abort()
  navigationController = undefined
  controller?.abort()
  controller = undefined
})
</script>
<template>
  <div
    class="document-preview"
    :aria-busy="busy"
  >
    <InkLoader v-if="busy" />
    <p
      v-if="error"
      role="alert"
    >
      {{ error }}
      <button
        type="button"
        :disabled="busy"
        @click="load(requestedUnit, requestedOffset)"
      >
        {{ $t('重试') }}
      </button>
    </p>
    <template v-if="page">
      <form
        class="document-search"
        @submit.prevent="lookup('search')"
      >
        <label class="document-query">
          {{ $t('在文档中查找') }}
          <input
            v-model="query"
            type="search"
            maxlength="200"
            :placeholder="$t('输入关键词')"
            @keydown.enter="$event.isComposing && $event.preventDefault()"
          />
        </label>
        <button
          type="submit"
          :disabled="navigationBusy || !query.trim()"
        >
          {{ $t('查找') }}
        </button>
        <button
          type="button"
          :disabled="navigationBusy"
          @click="lookup('outline')"
        >
          {{ $t('目录') }}
        </button>
      </form>
      <InkLoader v-if="navigationBusy" />
      <p
        v-if="navigationError"
        role="alert"
      >
        {{ navigationError }}
      </p>
      <section
        v-if="navigation"
        class="document-results"
        :aria-label="$t('文档定位结果')"
      >
        <p v-if="!navigation.items.length">
          {{ navigation.action === 'outline' ? $t('文档没有可用目录。') : $t('未找到匹配文字。') }}
        </p>
        <ol v-else>
          <li
            v-for="(item, index) in navigation.items"
            :key="index"
          >
            <button
              type="button"
              :disabled="busy"
              @click="load(item.unit, item.text_offset, 'text_layer')"
            >
              <span class="document-location">{{ $t(unitLabel) }} {{ item.unit }}</span>
              <span>{{ item.text }}</span>
            </button>
          </li>
        </ol>
        <p
          v-if="navigation.empty_text_units"
          class="document-label"
          v-text="$t('查找仅覆盖可提取的文字。')"
        />
        <p
          v-if="navigation.action === 'outline' && navigation.outline_truncated"
          class="document-label"
          v-text="$t('目录过长，仅显示部分条目。')"
        />
        <div class="document-navigation">
          <button
            v-if="navigationOffset"
            type="button"
            :disabled="navigationBusy"
            @click="lookup(navigation.action, Math.max(0, navigationOffset - 20))"
          >
            {{ $t('上一组') }}
          </button>
          <button
            v-if="navigation.next_offset !== null"
            type="button"
            :disabled="navigationBusy"
            @click="lookup(navigation.action, navigation.next_offset!)"
          >
            {{ $t('下一组') }}
          </button>
          <button
            type="button"
            @click="navigation = null"
          >
            {{ $t('收起') }}
          </button>
        </div>
      </section>
      <form
        class="document-navigation"
        @submit.prevent="jump"
      >
        <button
          type="button"
          :disabled="busy || page.unit <= 1"
          @click="load(page.unit - 1)"
        >
          {{ $t('上一项') }}
        </button>
        <label>
          {{ $t(unitLabel) }}
          <input
            v-model.number="target"
            type="number"
            min="1"
            :max="Math.max(1, page.total_units)"
            :disabled="busy || !page.total_units"
          />
          / {{ page.total_units }}
        </label>
        <button
          type="submit"
          :disabled="busy || !page.total_units"
        >
          {{ $t('跳转') }}
        </button>
        <button
          type="button"
          :disabled="busy || page.next_unit === null"
          @click="page.next_unit !== null && load(page.next_unit)"
        >
          {{ $t('下一项') }}
        </button>
      </form>
      <div
        v-if="page.format === 'pdf'"
        class="document-views"
        :aria-label="$t('查看方式')"
      >
        <button
          type="button"
          :aria-pressed="mode === 'text_layer'"
          :disabled="busy"
          @click="changeMode('text_layer')"
        >
          {{ $t('提取文字') }}
        </button>
        <button
          type="button"
          :aria-pressed="mode === 'render'"
          :disabled="busy"
          @click="changeMode('render')"
        >
          {{ $t('页面图像') }}
        </button>
        <button
          v-if="ocrReady"
          type="button"
          :aria-pressed="mode === 'ocr'"
          :disabled="busy"
          @click="changeMode('ocr')"
        >
          {{ $t('识别文字') }}
        </button>
      </div>
      <img
        v-if="imageUrl"
        class="document-page-image"
        :src="imageUrl"
        :alt="$t('文档页面')"
      />
      <template v-if="mode !== 'render'">
        <p
          v-if="mode === 'ocr'"
          class="document-label"
        >
          {{ $t('识别结果可能有误，请对照原图。') }}
        </p>
        <pre v-if="page.text">{{ page.text }}</pre>
        <p
          v-else
          v-text="$t('此处没有可提取的文字。')"
        />
        <p
          v-if="page.format === 'pdf' && !page.text && !ocrReady"
          class="document-label"
        >
          {{ $t('可查看页面图像；识别文字需要安装 Tesseract 和语言包。') }}
        </p>
      </template>
      <div
        v-if="mode !== 'render'"
        class="document-navigation"
      >
        <button
          v-if="page.text_offset"
          type="button"
          :disabled="busy"
          @click="load(page.unit)"
        >
          {{ $t('从头查看') }}
        </button>
        <button
          v-if="page.next_offset !== null"
          type="button"
          :disabled="busy"
          @click="load(page.unit, page.next_offset!)"
        >
          {{ $t('继续读取') }}
        </button>
      </div>
    </template>
  </div>
</template>
<style scoped lang="scss">
.document-preview {
  color: var(--ink);
  font-size: var(--text-sm);
}
.document-navigation {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}
.document-navigation label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
input {
  width: 5em;
  color: var(--ink);
  background: var(--paper);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  padding: var(--space-1);
}
button {
  color: var(--ink-2);
  background: var(--paper-2);
  border: var(--hairline) solid var(--rule);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
button:focus-visible,
input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  line-height: 1.7;
}
.document-label {
  color: var(--ink-3);
  font-size: var(--text-xs);
  margin: var(--space-3) 0;
}
.document-search {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.document-query {
  display: grid;
  gap: var(--space-1);
  flex: 1;
  min-width: 8em;
}
.document-query input {
  width: 100%;
  box-sizing: border-box;
}
.document-results {
  border-bottom: var(--hairline) solid var(--rule);
  padding-bottom: var(--space-3);
  margin-bottom: var(--space-3);
}
.document-results ol {
  list-style: none;
  margin: 0 0 var(--space-2);
  padding: 0;
  max-height: 16rem;
  overflow: auto;
}
.document-results li + li {
  margin-top: var(--space-1);
}
.document-results li button {
  display: grid;
  gap: var(--space-1);
  width: 100%;
  text-align: start;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.document-location {
  color: var(--ink-3);
  font-size: var(--text-xs);
}
</style>

<style scoped lang="scss">
.document-views {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: var(--space-3) 0;
  border-bottom: var(--hairline) solid var(--rule);
}
.document-views button {
  background: transparent;
  border: 0;
  border-radius: 0;
  border-bottom: 2px solid transparent;
}
.document-views button[aria-pressed='true'] {
  color: var(--ink);
  border-bottom-color: var(--accent);
}
.document-page-image {
  display: block;
  max-width: 100%;
  height: auto;
}
</style>
