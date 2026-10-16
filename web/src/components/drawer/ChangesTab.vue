<script setup lang="ts">
import WorkspaceDiff from '@/components/file-explorer/WorkspaceDiff.vue'
import { useUIStore } from '@/stores/ui'
const ui = useUIStore()
</script>
<template><WorkspaceDiff :active="ui.drawerOpen && ui.drawerTab === 'changes'" /></template>
