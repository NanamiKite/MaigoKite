import { t } from '../i18n'
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Notification,
  shell,
  type IpcMainInvokeEvent,
} from 'electron'
import path from 'node:path'
import { realpathSync } from 'node:fs'
import { appearanceValue, capsuleValue, notificationValue, pulseStates } from './contracts'
import type { Appearance } from '../stores/ui'
import type { PulseState } from '../api/events'
import type { GraphicsStatus } from './graphics-status'
export interface DesktopHost {
  main: () => BrowserWindow | null
  capsule: () => BrowserWindow | null
  allowed: (url: string) => boolean
  workspace: (folderId?: string) => string | Promise<string>
  appearance: () => Appearance | null
  setAppearance: (value: Appearance) => void
  pulse: (value: PulseState) => void
  graphics: () => GraphicsStatus
}
export function registerIPC(host: DesktopHost) {
  // Electron 43 defaults dialogs to Downloads instead of the last used folder.
  // Keep workspace selection anchored to the user's previous choice.
  let lastWorkspaceDirectory: string | undefined
  function trust(event: IpcMainInvokeEvent) {
    const window = BrowserWindow.fromWebContents(event.sender)
    if (
      !window ||
      ![host.main(), host.capsule()].includes(window) ||
      !event.senderFrame ||
      event.senderFrame !== event.sender.mainFrame ||
      !host.allowed(event.senderFrame.url)
    )
      throw new Error(t('不允许此窗口访问桌面能力。'))
    return window
  }
  ipcMain.handle('runtime:status', (event) => {
    trust(event)
    return { platform: process.platform, packaged: app.isPackaged }
  })
  ipcMain.handle('graphics:status', (event) => {
    trust(event)
    return host.graphics()
  })
  ipcMain.handle('workspace:choose-folder', async (event) => {
    const window = trust(event)
    if (window !== host.main()) throw new Error(t('请从主窗口选择工作区。'))
    if (!lastWorkspaceDirectory) {
      try {
        lastWorkspaceDirectory = await host.workspace()
      } catch {
        // A missing workspace/backend must not prevent selecting a replacement folder.
        lastWorkspaceDirectory = app.getPath('home')
      }
    }
    const result = await dialog.showOpenDialog(window, {
      title: t('选择工作区文件夹'),
      defaultPath: lastWorkspaceDirectory,
      properties: ['openDirectory'],
    })
    if (result.canceled || !result.filePaths[0]) return null
    lastWorkspaceDirectory = realpathSync(result.filePaths[0])
    return lastWorkspaceDirectory
  })
  ipcMain.handle('window:control', (event, action: unknown) => {
    const window = trust(event)
    if (action === 'minimize') window.minimize()
    else if (action === 'maximize') window.isMaximized() ? window.unmaximize() : window.maximize()
    else if (action === 'close') window.close()
    else if (action === 'hide') window.hide()
    else throw new Error(t('未知窗口操作。'))
  })
  ipcMain.handle('appearance:get', (event) => {
    trust(event)
    return host.appearance()
  })
  ipcMain.handle('appearance:set', (event, value: unknown) => {
    trust(event)
    if (!appearanceValue(value)) throw new Error(t('外观或快捷键格式不正确。'))
    host.setAppearance(value)
  })
  ipcMain.handle('tray:pulse', (event, value: unknown) => {
    trust(event)
    if (typeof value !== 'string' || !pulseStates.some((state) => state === value))
      throw new Error(t('未知状态。'))
    host.pulse(value as PulseState)
  })
  ipcMain.handle('path:open', async (event, value: unknown, folderId: unknown = '') => {
    trust(event)
    if (typeof value !== 'string' || !value || value.length > 4096 || value.includes('\0'))
      throw new Error(t('路径不正确。'))
    if (typeof folderId !== 'string' || folderId.length > 200 || folderId.includes('\0'))
      throw new Error(t('工作区不正确。'))
    const root = realpathSync(await host.workspace(folderId)),
      target = realpathSync(path.resolve(root, value))
    if (target !== root && !target.startsWith(root + path.sep))
      throw new Error(t('只能打开当前工作区内的文件。'))
    return shell.openPath(target)
  })
  ipcMain.handle('capsule:send', (event, value: unknown) => {
    const window = trust(event)
    if (window !== host.capsule() || !capsuleValue(value)) throw new Error(t('速记内容不正确。'))
    const main = host.main()
    if (!main || main.webContents.isLoading()) throw new Error(t('助手正在启动，请稍后发送。'))
    main.webContents.send('capsule:request', value)
    window.hide()
  })
  ipcMain.handle('notification:show', (event, value: unknown) => {
    trust(event)
    if (!notificationValue(value)) throw new Error(t('通知内容不正确。'))
    if (!Notification.isSupported()) return
    const notification = new Notification({
      title: value.title,
      body: value.body,
      actions:
        process.platform === 'darwin' && value.approvalId
          ? [{ type: 'button', text: t('允许') }]
          : [],
    })
    notification.on('failed', () => {
      // macOS 42+ requires signing; denial by the OS leaves the in-app approval intact.
      console.warn('[MaigoKite] Native notification unavailable; use the in-app conversation.')
    })
    notification.on('click', () => {
      host.main()?.show()
      host.main()?.focus()
      host.main()?.webContents.send('notification:action', {
        sessionId: value.sessionId,
        approvalId: value.approvalId,
        allow: false,
      })
    })
    notification.on('action', () => {
      host.main()?.webContents.send('notification:action', {
        sessionId: value.sessionId,
        approvalId: value.approvalId,
        allow: true,
      })
    })
    notification.show()
  })
}
