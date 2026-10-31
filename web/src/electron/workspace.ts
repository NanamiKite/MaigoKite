import { t } from '../i18n'
import { realpathSync } from 'node:fs'
import path from 'node:path'
import { backendURL, desktopHeaders } from './desktop-access'

/** Resolve an ID through the local API so the renderer cannot choose an arbitrary root. */
export async function registeredWorkspaceRoot(
  folderId: string,
  desktopToken = '',
): Promise<string> {
  const base = new URL(process.env.MAIGOKITE_BACKEND_URL || backendURL)
  if (
    !['http:', 'https:'].includes(base.protocol) ||
    !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) ||
    base.username ||
    base.password
  )
    throw new Error(t('只能打开本机工作区的文件。'))
  const url = new URL('/api/v1/workspaces/personal/folders', base)
  const response = await fetch(url, {
    headers: desktopHeaders(url.href, desktopToken),
    signal: AbortSignal.timeout(5000),
    redirect: 'error',
  })
  if (!response.ok) throw new Error(t('无法确认工作区，请检查本地服务。'))
  const data: unknown = await response.json()
  const folders =
    data && typeof data === 'object' ? (data as Record<string, unknown>).folders : undefined
  if (!Array.isArray(folders)) throw new Error(t('工作区信息不正确。'))
  for (const value of folders as unknown[]) {
    if (!value || typeof value !== 'object') continue
    const item = value as Record<string, unknown>
    if (
      (item.id === undefined ? '' : item.id) !== folderId ||
      typeof item.root_path !== 'string' ||
      !path.isAbsolute(item.root_path)
    )
      continue
    const root = realpathSync(item.root_path)
    if (root !== item.root_path) throw new Error(t('工作区路径已变更，请重新选择文件夹。'))
    return root
  }
  throw new Error(t('工作区不存在，请重新选择文件夹。'))
}
