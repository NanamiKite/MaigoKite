import { t } from '@/i18n'
import type { AwaitItem } from '@/api/events'
import type { Approval, ApprovalScope } from '@/api/client'

export function approvalScopeLabel(scope: ApprovalScope, resource: Record<string, unknown> = {}) {
  if (scope === 'session_public_web') return t('本会话搜索和公共网页读取')
  if (scope === 'once') return t('仅这一次')
  if (resource.tool === 'web_search')
    return t(scope === 'task' ? '本任务仅此搜索服务' : '本会话仅此搜索服务')
  if (typeof resource.origin === 'string')
    return t(scope === 'task' ? '本任务仅此站点' : '本会话仅此站点')
  return t(scope === 'task' ? '本任务内同范围操作' : '本会话内同范围操作')
}

/** Display the server's exact scope; never infer or broaden an authorization. */
export function approvalScopeDescription(
  scope: Record<string, unknown> = {},
  authorizationScope?: ApprovalScope,
) {
  const lines: string[] = []
  if (typeof scope.attachment_sources === 'string')
    lines.push(t('本次授权仅适用于当前附件；添加新附件后需重新确认。'))
  if (authorizationScope === 'session_public_web') {
    lines.push(
      t('允许本会话使用已配置的搜索服务，并读取不同站点的公共网页。'),
      t('不包含浏览器操作、登录、提交、上传或命令联网。'),
      t('搜索服务会收到查询内容，目标网站会收到网页访问请求。'),
    )
    if (typeof scope.workspace_root === 'string')
      lines.push(t('工作区：{0}', [scope.workspace_root]))
    return lines
  }
  if (typeof scope.tool === 'string') lines.push(t('工具：{0}', [scope.tool]))
  if (typeof scope.directory === 'string')
    lines.push(
      t(scope.recursive === false ? '目录：{0}（不含子目录）' : '目录：{0}', [scope.directory]),
    )
  if (typeof scope.origin === 'string') lines.push(t('目标站点：{0}', [scope.origin]))
  if (typeof scope.engine === 'string') lines.push(t('搜索服务：{0}', [scope.engine]))
  if (Array.isArray(scope.argv)) lines.push(t('固定命令参数：{0}', [JSON.stringify(scope.argv)]))
  if (typeof scope.workdir === 'string') lines.push(t('执行目录：{0}', [scope.workdir]))
  if (typeof scope.workspace_root === 'string') lines.push(t('工作区：{0}', [scope.workspace_root]))
  return lines
}
/** Describes known effects, never authorizes a tool or predicts arbitrary code. */
export function approvalDescription(item: AwaitItem, record?: Approval) {
  let input: Record<string, unknown> = record?.arguments || {}
  if (!record) {
    try {
      const value: unknown = JSON.parse(item.detail)
      if (value && typeof value === 'object' && !Array.isArray(value))
        input = value as Record<string, unknown>
    } catch {
      /* Plain resource scopes are kept as targets, not parsed as commands. */
    }
  }
  const tool = record?.tool_name || item.title.split(' ').slice(-1)[0] || ''
  const descriptions: Record<string, [string, string]> = {
    file_write: ['写入文件', '将更改目标文件的内容。'],
    file_edit: ['修改文件', '将更改目标文件的内容。'],
    file_read: ['读取文件', '读取内容，不修改文件。'],
    bash: ['运行命令', '将在目标目录运行命令。'],
    exec_command: ['运行命令', '将在目标目录运行命令。'],
    browser: ['浏览器操作', '操作目标页面，可能改变页面状态。'],
    web_search: ['搜索网页', '将查询内容发送给搜索服务，获取相关网页。'],
    web_fetch: ['读取网页', '读取目标网页的文字内容，不使用浏览器登录态。'],
  }
  const descriptor = descriptions[tool]
  const target = [
    input.path,
    input.cwd,
    input.url,
    tool === 'web_search' ? input.query : undefined,
    record?.resource_scope,
  ].find((value) => typeof value === 'string' && value.trim())
  return {
    action: descriptor ? t(descriptor[0]) : t('执行操作'),
    target: typeof target === 'string' ? target : t('未指定目标'),
    impact: descriptor ? t(descriptor[1]) : t('请查看操作详情，确认后继续。'),
    tool,
    command: typeof input.command === 'string' ? input.command : '',
  }
}
