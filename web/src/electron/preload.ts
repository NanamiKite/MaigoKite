import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { Appearance } from '../stores/ui'
import type { PulseState } from '../api/events'
import { appearanceValue, capsuleValue, notificationValue, pulseStates } from './contracts'
import { graphicsStatusValue, type GraphicsStatus } from './graphics-status'
export interface CapsuleRequest {
  content: string
  newSession: boolean
}
export interface DesktopNotification {
  title: string
  body: string
  sessionId?: string
  approvalId?: string
}
function listen<T>(
  channel: string,
  valid: (value: unknown) => value is T,
  callback: (value: T) => void,
) {
  const listener = (_event: IpcRendererEvent, value: unknown) => {
    if (valid(value)) callback(value)
  }
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}
const string = (value: unknown): value is string => typeof value === 'string'
const electronAPI = {
  chooseWorkspaceFolder: () =>
    ipcRenderer.invoke('workspace:choose-folder') as Promise<string | null>,
  runtimeStatus: () =>
    ipcRenderer.invoke('runtime:status') as Promise<{ platform: string; packaged: boolean }>,
  onRuntimeError: (callback: (message: string) => void) =>
    listen('runtime:error', string, callback),
  getGraphicsStatus: async (): Promise<GraphicsStatus> => {
    const value: unknown = await ipcRenderer.invoke('graphics:status')
    if (!graphicsStatusValue(value)) throw new Error('Invalid graphics status')
    return value
  },
  onGraphicsStatus: (callback: (value: GraphicsStatus) => void) =>
    listen('graphics:changed', graphicsStatusValue, callback),
  windowControl: (action: 'minimize' | 'maximize' | 'close' | 'hide') => {
    if (!['minimize', 'maximize', 'close', 'hide'].includes(action))
      return Promise.reject(new Error('未知窗口操作'))
    return ipcRenderer.invoke('window:control', action) as Promise<void>
  },
  getAppearance: () => ipcRenderer.invoke('appearance:get') as Promise<Appearance | null>,
  setAppearance: (value: Appearance) => {
    if (!appearanceValue(value)) return Promise.reject(new Error('外观格式不正确'))
    return ipcRenderer.invoke('appearance:set', value) as Promise<void>
  },
  onAppearance: (callback: (value: Appearance) => void) =>
    listen('appearance:changed', appearanceValue, callback),
  setPulse: (value: PulseState) => {
    if (!pulseStates.includes(value)) return Promise.reject(new Error('未知状态'))
    return ipcRenderer.invoke('tray:pulse', value) as Promise<void>
  },
  notify: (value: DesktopNotification) => {
    if (!notificationValue(value)) return Promise.reject(new Error('通知格式不正确'))
    return ipcRenderer.invoke('notification:show', value) as Promise<void>
  },
  openPath: (value: string, folderId = '') => {
    if (!string(value) || value.length > 4096 || value.includes('\0'))
      return Promise.reject(new Error('路径不正确'))
    if (!string(folderId) || folderId.length > 200 || folderId.includes('\0'))
      return Promise.reject(new Error('工作区不正确'))
    return ipcRenderer.invoke('path:open', value, folderId) as Promise<string>
  },
  sendCapsule: (value: CapsuleRequest) => {
    if (!capsuleValue(value)) return Promise.reject(new Error('速记格式不正确'))
    return ipcRenderer.invoke('capsule:send', value) as Promise<void>
  },
  onCapsule: (callback: (value: CapsuleRequest) => void) =>
    listen('capsule:request', capsuleValue, callback),
  onNotification: (
    callback: (value: { sessionId?: string; approvalId?: string; allow: boolean }) => void,
  ) =>
    listen(
      'notification:action',
      (value): value is { sessionId?: string; approvalId?: string; allow: boolean } => {
        if (!value || typeof value !== 'object') return false
        const p = value as Record<string, unknown>
        return (
          typeof p.allow === 'boolean' &&
          (p.sessionId === undefined || string(p.sessionId)) &&
          (p.approvalId === undefined || string(p.approvalId))
        )
      },
      callback,
    ),
}
export type ElectronAPI = typeof electronAPI
contextBridge.exposeInMainWorld('electronAPI', electronAPI)
