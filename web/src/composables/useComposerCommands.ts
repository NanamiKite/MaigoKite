import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, type InstalledSkill } from '@/api/client'
import { useUIStore } from '@/stores/ui'
import type { SlashCommand } from '@/components/inkwell/SlashMenu.vue'
import { t } from '@/i18n'

interface ComposerCommand extends SlashCommand {
  run: () => void
}

export function useComposerCommands(options: {
  open: () => boolean
  imageDisabled: () => boolean
  isSending: () => boolean
  image: () => void
  reference: () => void
  stop: () => void
  insert: (text: string) => void
}) {
  const ui = useUIStore(),
    router = useRouter()
  const skills = ref<InstalledSkill[]>([]),
    loading = ref(false),
    error = ref('')
  const revision = ref(0)
  // Read the applied registry on each opening; saved-but-disabled Skills stay out.
  watch([options.open, revision], async ([open], _, onCleanup) => {
    if (!open) return
    const request = new AbortController()
    onCleanup(() => request.abort())
    skills.value = []
    error.value = ''
    loading.value = true
    try {
      const items = await api.listSkills(request.signal)
      if (!request.signal.aborted) skills.value = items
    } catch (reason) {
      if (!request.signal.aborted)
        error.value = t('无法读取技能列表：{0}', [
          reason instanceof Error ? reason.message : String(reason),
        ])
    } finally {
      if (!request.signal.aborted) loading.value = false
    }
  })
  const commands = computed<ComposerCommand[]>(() => [
    {
      id: 'image',
      label: '添加图片',
      icon: 'image',
      group: '添加',
      run: options.image,
      disabled: options.imageDisabled(),
      reason: options.isSending() ? t('请等待当前任务结束后再发送图片。') : undefined,
    },
    {
      id: 'reference',
      label: '引用文件、会话或记忆',
      icon: 'at',
      group: '添加',
      run: options.reference,
    },
    {
      id: 'new',
      label: '新页',
      icon: 'compose',
      group: '操作',
      run: () => {
        void router.push('/chat')
      },
    },
    {
      id: 'stop',
      label: '中断任务',
      icon: 'stop',
      group: '操作',
      disabled: !options.isSending(),
      run: options.stop,
    },
    {
      id: 'files',
      label: '工作区文件',
      icon: 'files',
      group: '操作',
      run: () => ui.openDrawer('files'),
    },
    {
      id: 'changes',
      label: '查看变更',
      icon: 'review',
      group: '操作',
      run: () => ui.openDrawer('changes'),
    },
    {
      id: 'plan',
      label: '查看计划',
      icon: 'target',
      group: '操作',
      run: () => ui.openDrawer('plan'),
    },
    {
      id: 'jobs',
      label: '运行',
      icon: 'activity',
      group: '操作',
      run: () => ui.openDrawer('jobs'),
    },
    {
      id: 'pending',
      label: '等你处理',
      icon: 'pending',
      group: '操作',
      run: () => ui.openDrawer('pending'),
    },
    {
      id: 'help',
      label: '快捷键表',
      icon: 'info',
      group: '操作',
      run: () => {
        ui.shortcutsOpen = true
      },
    },
    {
      id: 'memory',
      label: '它记得',
      icon: 'brain',
      group: '设置',
      run: () => {
        void router.push('/memory')
      },
    },
    {
      id: 'models',
      label: '模型',
      icon: 'brain',
      group: '设置',
      run: () => {
        void router.push('/settings?tab=models')
      },
    },
    {
      id: 'permissions',
      label: '权限',
      icon: 'shield',
      group: '设置',
      run: () => {
        void router.push('/settings?tab=permissions')
      },
    },
    {
      id: 'tools',
      label: '工具',
      icon: 'files',
      group: '设置',
      run: () => {
        void router.push('/settings?tab=tools')
      },
    },
    {
      id: 'subagents',
      label: '子 Agent',
      icon: 'subagents',
      group: '设置',
      run: () => {
        void router.push('/settings?tab=subagents')
      },
    },
    {
      id: 'usage',
      label: '用量统计',
      icon: 'activity',
      group: '设置',
      run: () => {
        void router.push('/settings?tab=statistics')
      },
    },
    {
      id: 'extensions',
      label: '管理 MCP 与 Skill 扩展',
      icon: 'cube',
      group: '设置',
      run: () => {
        void router.push('/integrations')
      },
    },
    {
      id: 'settings',
      label: '设置',
      icon: 'settings',
      group: '设置',
      run: () => {
        void router.push('/settings')
      },
    },
    ...skills.value.map((skill): ComposerCommand => ({
      id: `skill/${skill.name}`,
      label: skill.name,
      icon: 'file',
      group: '技能',
      keywords: skill.description,
      run: () => options.insert(t('请先读取并使用技能「{0}」，完成以下任务：', [skill.name])),
    })),
  ])
  function select(item: SlashCommand) {
    const command = commands.value.find((entry) => entry.id === item.id)
    if (command && !command.disabled) command.run()
  }
  return {
    commands,
    select,
    loading,
    error,
    retry: () => {
      revision.value++
    },
  }
}
