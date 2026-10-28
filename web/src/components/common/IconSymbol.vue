<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    name: string
    size?: number
    strokeWidth?: number
  }>(),
  {
    size: 18,
    strokeWidth: 1.8,
  },
)

const paths: Record<string, string> = {
  copy: 'M9 9h11v11H9V9ZM15 9V4H4v11h5',
  branch:
    'M6 7v10M18 7v3a4 4 0 0 1-4 4H6M8 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM8 19a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM20 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z',
  at: 'M16 8v7c0 3 5 1 5-3a9 9 0 1 0-4 7M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  pending: 'm12 3 9 9-9 9-9-9 9-9Z',
  home: 'M3.5 10.7 12 3.6l8.5 7.1v8.2a1.6 1.6 0 0 1-1.6 1.6H5.1a1.6 1.6 0 0 1-1.6-1.6v-8.2ZM9 20.5v-6.2h6v6.2',
  compose:
    'M12 20.5H5a1.5 1.5 0 0 1-1.5-1.5V5A1.5 1.5 0 0 1 5 3.5h8M14.2 5.8l4-4 3 3-4 4L12 10l1.2-4.2Z',
  search: 'm20.5 20.5-4.6-4.6M10.8 18a7.2 7.2 0 1 1 0-14.4 7.2 7.2 0 0 1 0 14.4Z',
  activity: 'M3 12h3.2l2.1-5.2 4 10.4 2.4-7 1.8 1.8H21',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4.6l3 1.8',
  globe:
    'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-18c2.2 2.4 3.4 5.4 3.4 9S14.2 18.6 12 21M3.4 9h17.2M3.4 15h17.2M12 3C9.8 5.4 8.6 8.4 8.6 12s1.2 6.6 3.4 9',
  cube: 'm12 2.8 8 4.6v9.2l-8 4.6-8-4.6V7.4l8-4.6Zm-8 4.6 8 4.7 8-4.7M12 21.2v-9.1',
  sparkles:
    'm12 2 1.1 3.4a7.1 7.1 0 0 0 4.5 4.5L21 11l-3.4 1.1a7.1 7.1 0 0 0-4.5 4.5L12 20l-1.1-3.4a7.1 7.1 0 0 0-4.5-4.5L3 11l3.4-1.1a7.1 7.1 0 0 0 4.5-4.5L12 2Z',
  review:
    'M6 3.5h12A1.5 1.5 0 0 1 19.5 5v14a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5ZM8 8h8M8 12h5M8 16h7',
  target:
    'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-4.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0-2.6a1.9 1.9 0 1 0 0-3.8 1.9 1.9 0 0 0 0 3.8Z',
  archive: 'M4 7.5h16v12H4v-12ZM3 3.5h18v4H3v-4Zm6.5 8h5',
  shield: 'M12 21s7-3.5 7-10V5l-7-2.5L5 5v6c0 6.5 7 10 7 10Zm-3-9 2 2 4-4',
  settings:
    'M12 15.4a3.4 3.4 0 1 0 0-6.8 3.4 3.4 0 0 0 0 6.8Zm7.2-3.4c0-.5-.1-1-.2-1.5l2-1.5-2-3.4-2.4 1a8 8 0 0 0-2.6-1.5L13.7 2h-3.9l-.4 3.1c-.9.3-1.7.8-2.5 1.5l-2.4-1-2 3.4 2 1.5a8 8 0 0 0 0 3L2.5 15l2 3.4 2.4-1c.8.7 1.6 1.2 2.5 1.5l.4 3.1h3.9l.4-3.1c.9-.3 1.7-.8 2.5-1.5l2.4 1 2-3.4-2-1.5c.1-.5.2-1 .2-1.5Z',
  sidebar: 'M4 4h16v16H4V4Zm5 0v16',
  subagents: 'M9 3h6v6H9V3ZM4 15h6v6H4v-6Zm10 0h6v6h-6v-6ZM12 9v3M7 15v-3h10v3',
  bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5',
  moon: 'M20.5 13.2A8.8 8.8 0 0 1 10.8 3.5a9 9 0 1 0 9.7 9.7Z',
  minimize: 'M5 12h14',
  maximize: 'M5 5h14v14H5V5Z',
  expand: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
  restore: 'M9 3v6H3m12-6v6h6M9 21v-6H3m12 6v-6h6',
  close: 'm6 6 12 12M18 6 6 18',
  trash: 'M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7',
  files:
    'M5 3.5h8l2 2H19a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V5A1.5 1.5 0 0 1 5 3.5Z',
  folder:
    'M3.5 6.5A1.5 1.5 0 0 1 5 5h5l2 2h7A1.5 1.5 0 0 1 20.5 8.5V18a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18V6.5Z',
  file: 'M6 2.8h8l4 4V21H6V2.8Zm8 0v4h4',
  'file-plus': 'M6 2.8h8l4 4V21H6V2.8Zm8 0v4h4M12 10v7m-3.5-3.5h7',
  'folder-plus':
    'M3.5 6.5A1.5 1.5 0 0 1 5 5h5l2 2h7A1.5 1.5 0 0 1 20.5 8.5V18a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18V6.5ZM12 10v6m-3-3h6',
  'folder-open':
    'M3.5 16V6.5A1.5 1.5 0 0 1 5 5h5l2 2h7A1.5 1.5 0 0 1 20.5 8.5V10M3.5 18l3-8H22l-3 8H3.5Z',
  refresh: 'M20 7v5h-5M4 17v-5h5M18.4 9A7 7 0 0 0 6.2 6.4L4 9m16 6-2.2 2.6A7 7 0 0 1 5.6 15',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  grip: 'M9 5h.01M15 5h.01M9 12h.01M15 12h.01M9 19h.01M15 19h.01',
  send: 'm21 3-7.2 18-3.1-7.7L3 10.2 21 3Zm-10.3 10.3L21 3',
  stop: 'M7 7h10v10H7z',
  plus: 'M12 5v14M5 12h14',
  image: 'M4 4h16v16H4V4Zm0 12 5-5 4 4 3-3 4 4M16 8h.01',
  'arrow-up': 'M12 20V4m-6 6 6-6 6 6',
  edit: 'm15 4 5 5M4 20l1-5L17 3l4 4L9 19l-5 1Z',
  chevron: 'm9 18 6-6-6-6',
  terminal: 'm5 7 4 4-4 4m6 0h8',
  check: 'm5 12 4.2 4.2L19 6.5',
  alert: 'M12 9v4m0 4h.01M10.4 3.8 2.1 17.4a1.8 1.8 0 0 0 3.2 0l8.3-13.6a1.8 1.8 0 0 0-3.2 0Z',
  brain:
    'M9.5 4.2A3.5 3.5 0 0 0 5 7.5v.3A3.8 3.8 0 0 0 4 15a3.5 3.5 0 0 0 5.5 4.8M14.5 4.2A3.5 3.5 0 0 1 19 7.5v.3a3.8 3.8 0 0 1 1 7.2 3.5 3.5 0 0 1-5.5 4.8M9.5 4.2v15.6m5-15.6v15.6M7 10h2.5m5 4H17',
  bolt: 'm13 2-8 12h7l-1 8 8-12h-7l1-8Z',
  calendar: 'M5 4.5h14A1.5 1.5 0 0 1 20.5 6v14H3.5V6A1.5 1.5 0 0 1 5 4.5ZM8 2v5m8-5v5M3.5 9h17',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-10v6m0-10h.01',
  key: 'M14.5 9.5a5 5 0 1 1-2.2 4.1L21 5v4h-3v3h-3l-1.2 1.2M6.5 14.5h.01',
  eye: 'M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Zm9.5 3.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z',
  'eye-off':
    'm3 3 18 18M10.6 6.1A10.8 10.8 0 0 1 12 6c6 0 9.5 6 9.5 6a15.4 15.4 0 0 1-2.1 2.8M6.2 6.2C3.8 7.8 2.5 12 2.5 12S6 18 12 18c1 0 2-.2 2.8-.5m-5-7.7a3.2 3.2 0 0 0 4.4 4.4',
}

const pathData = computed(() => paths[props.name] ?? paths.sparkles)
</script>

<template>
  <svg
    class="icon-symbol"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="pathData" />
  </svg>
</template>

<style scoped>
.icon-symbol {
  display: block;
  flex: none;
}
</style>
