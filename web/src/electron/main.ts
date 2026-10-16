import {
  app,
  BrowserWindow,
  dialog,
  globalShortcut,
  Menu,
  screen,
  shell,
  Tray,
  type WebPreferences,
} from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { createServer } from 'node:net'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'
import { createRuntimeEnvironment } from './runtime-env'
import { resolveCorePython } from './python-runtime'
import { registerIPC } from './ipc'
import { registeredWorkspaceRoot } from './workspace'
import { appearanceValue } from './contracts'
import { trayImage } from './tray-icons'
import { i18n, resolveLanguage, t } from '../i18n'
import type { Appearance } from '../stores/ui'
import type { PulseState } from '../api/events'
import type { GraphicsStatus } from './graphics-status'
app.setName('MaigoKite')

let mainWindow: BrowserWindow | null = null,
  capsuleWindow: BrowserWindow | null = null,
  tray: Tray | null = null
let shutdownComplete = false
const singleInstance = app.requestSingleInstanceLock()
if (!singleInstance) app.quit()
let quitting = false,
  appearance: Appearance | null = null,
  shortcut = ''
const appearancePath = path.join(app.getPath('userData'), 'inkline-ui.json')
try {
  const value: unknown = JSON.parse(readFileSync(appearancePath, 'utf8'))
  if (appearanceValue(value)) appearance = value
} catch {
  /* Missing or invalid preferences keep Electron's default acceleration policy. */
}
// Electron requires this choice before ready; changing preferences cannot alter this run.
const hardwareAccelerationAtStartup = appearance?.hardwareAcceleration !== false
if (singleInstance && !hardwareAccelerationAtStartup) app.disableHardwareAcceleration()
let graphicsState: GraphicsStatus['state'] = 'pending'
let graphicsFeatures: GraphicsStatus['features'] = {
  compositing: null,
  rasterization: null,
  webgl: null,
}
function graphicsStatus(): GraphicsStatus {
  return {
    state: graphicsState,
    enabledAtStartup: hardwareAccelerationAtStartup,
    preferredEnabled: appearance?.hardwareAcceleration !== false,
    features: { ...graphicsFeatures },
  }
}
function publishGraphicsStatus() {
  for (const window of [mainWindow, capsuleWindow]) {
    if (window && !window.isDestroyed() && !window.webContents.isDestroyed())
      window.webContents.send('graphics:changed', graphicsStatus())
  }
}
app.on('gpu-info-update', () => {
  // Before this event Chromium may return provisional software-only values.
  try {
    const features = app.getGPUFeatureStatus()
    graphicsFeatures = {
      compositing: features.gpu_compositing ?? null,
      rasterization: features.rasterization ?? null,
      webgl: features.webgl ?? null,
    }
    graphicsState = 'ready'
  } catch {
    graphicsFeatures = { compositing: null, rasterization: null, webgl: null }
    graphicsState = 'unavailable'
  }
  publishGraphicsStatus()
})
app.on('child-process-gone', (_event, details) => {
  if (details.type !== 'GPU') return
  graphicsFeatures = { compositing: null, rasterization: null, webgl: null }
  graphicsState = 'unavailable'
  publishGraphicsStatus()
})
let pulseState: PulseState = 'idle'
const children: ChildProcess[] = []
const runtimeStartup = new AbortController()
let runtimeFailure = '',
  runtimeShutdown: Promise<void> | null = null
const indexPath = path.resolve(__dirname, '../dist/index.html')
const builtUI = app.isPackaged || process.env.MAIGOKITE_DESKTOP_BUILT === '1'
const developmentURL = 'http://127.0.0.1:5173'
const appIconPath = path.resolve(
  __dirname,
  app.isPackaged
    ? '../dist/brand/app-icon-cat-ribbon-v1.png'
    : '../public/brand/app-icon-cat-ribbon-v1.png',
)
export function secureWebPreferences(): WebPreferences {
  return {
    preload: path.join(__dirname, 'preload.cjs'),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
  }
}
function projectRoot() {
  return process.env.MAIGOKITE_PROJECT_ROOT || path.resolve(app.getAppPath(), '..')
}
function absoluteHostPath(value: string) {
  if (value === '~') return app.getPath('home')
  if (value.startsWith('~/') || value.startsWith('~\\'))
    return path.resolve(app.getPath('home'), value.slice(2))
  return path.resolve(value)
}
function installationRoot() {
  if (process.env.MAIGOKITE_INSTALL_DIR) return absoluteHostPath(process.env.MAIGOKITE_INSTALL_DIR)
  if (!app.isPackaged) return path.resolve(projectRoot())
  // AppImage's executable is mounted read-only; use the original image's directory.
  if (process.platform === 'linux' && process.env.APPIMAGE)
    return path.dirname(path.resolve(process.env.APPIMAGE))
  const executableDirectory = path.dirname(app.getPath('exe'))
  // Keep user files outside the signed .app bundle, not in Contents/MacOS or app.asar.
  return process.platform === 'darwin'
    ? path.resolve(executableDirectory, '../../..')
    : executableDirectory
}
function reportRuntimeFailure(message: string) {
  if (quitting || runtimeFailure) return
  runtimeFailure = message
  runtimeStartup.abort(new Error(message))
  console.error(`[MaigoKite] ${message}`)
  mainWindow?.webContents.send('runtime:error', message)
  // A failed backend ends this owned desktop suite too, rather than leaving
  // Electron/Vite alive against a stopped runtime.
  void stopRuntime().finally(() => app.exit(1))
  dialog.showErrorBox(t('应用服务无法启动'), message)
}
function startProcess(
  name: string,
  command: string,
  args: string[],
  cwd: string,
  configPath?: string,
) {
  runtimeStartup.signal.throwIfAborted()
  const env = createRuntimeEnvironment()
  env.MAIGOKITE_INSTALL_DIR = installationRoot()
  if (env.MAIGOKITE_WORKSPACE_ROOT)
    env.MAIGOKITE_WORKSPACE_ROOT = absoluteHostPath(env.MAIGOKITE_WORKSPACE_ROOT)
  if (configPath) env.MAIGOKITE_CONFIG = configPath
  env.MAIGOKITE_PARENT_PIPE = '1'
  console.info(`[MaigoKite] Starting ${name}`)
  // The process group includes subprocesses launched by the backend's tools.
  const child = spawn(command, args, {
    cwd,
    shell: false,
    detached: process.platform !== 'win32',
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
    env,
  })
  // Forward logs; `close` waits for inherited pipes before cleanup is considered finished.
  child.stdout?.on('data', (chunk: Buffer) => process.stdout.write(chunk))
  child.stderr?.on('data', (chunk: Buffer) => process.stderr.write(chunk))
  child.stdin?.on('error', () => {
    /* The child may fail before its owner closes stdin. */
  })
  child.on('error', (error) => reportRuntimeFailure(t('{0} 启动失败：{1}', [name, error.message])))
  child.on('exit', (code, signal) => {
    if (!quitting && !runtimeShutdown)
      reportRuntimeFailure(
        t('{0} 已退出（{1}）。请查看终端日志后重新启动。', [name, signal || String(code)]),
      )
  })
  child.once('close', () => {
    const index = children.indexOf(child)
    if (index >= 0) children.splice(index, 1)
  })
  children.push(child)
}
async function waitForService(service: 'Backend' | 'Web') {
  const backend = service === 'Backend'
  const port = backend ? 8080 : 5173
  const signal = runtimeStartup.signal,
    deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    signal.throwIfAborted()
    // A listening socket alone does not mean recovery or Vite initialization has finished.
    let ready = false
    try {
      const response = await fetch(backend ? 'http://127.0.0.1:8080/health' : developmentURL, {
        method: backend ? 'GET' : 'HEAD',
        redirect: 'error',
        signal: AbortSignal.any([signal, AbortSignal.timeout(1_000)]),
      })
      ready = response.ok && (!backend || (await response.json()).status === 'ok')
    } catch {
      /* Retry while initialization is still in progress. */
    }
    signal.throwIfAborted()
    if (ready) return
    await delay(250, undefined, { signal })
  }
  throw new Error(t('{0} 启动超时（端口 {1}）。请查看终端日志。', [service, port]))
}
async function startRuntime() {
  if (process.env.MAIGOKITE_EXTERNAL_RUNTIME === '1' || process.env.VITE_MOCK === '1') return
  // Do not attach to a previous instance's open TCP port or mutate its database.
  for (const port of [8080])
    await new Promise<void>((resolve, reject) => {
      const server = createServer()
      server.once('error', () =>
        reject(new Error(t('端口 {0} 已被占用，请先退出已有应用服务。', [port]))),
      )
      server.listen({ host: '127.0.0.1', port, exclusive: true }, () =>
        server.close(() => resolve()),
      )
    })
  const root = projectRoot()
  const bundledConfig = path.join(process.resourcesPath, 'config', 'config.prod.yaml')
  const configPath = process.env.MAIGOKITE_CONFIG
    ? absoluteHostPath(process.env.MAIGOKITE_CONFIG)
    : app.isPackaged
      ? existsSync(bundledConfig)
        ? bundledConfig
        : undefined
      : path.join(root, 'config', 'config.dev.yaml')
  if (app.isPackaged) {
    const executableSuffix = process.platform === 'win32' ? '.exe' : ''
    startProcess(
      'Backend',
      path.join(process.resourcesPath, 'bin', `agent-core${executableSuffix}`),
      ['run', '--port', '8080'],
      process.resourcesPath,
      configPath,
    )
  } else {
    const python = await resolveCorePython(root, runtimeStartup.signal)
    runtimeStartup.signal.throwIfAborted()
    console.info(`[MaigoKite] Core Python: ${python}`)
    startProcess(
      'Backend',
      python,
      [path.join(root, 'python', 'main.py'), 'run', '--port', '8080'],
      root,
      configPath,
    )
  }
  await waitForService('Backend')
  console.info('[MaigoKite] Backend is ready')
}
function stopRuntime(): Promise<void> {
  if (runtimeShutdown) return runtimeShutdown
  runtimeStartup.abort()
  runtimeShutdown = Promise.all(
    children.splice(0).map(
      (child) =>
        new Promise<void>((resolve) => {
          const pid = child.pid
          if (!pid) {
            resolve()
            return
          }
          function terminate(processId: number, signal: NodeJS.Signals) {
            try {
              if (process.platform === 'win32') {
                const killer = spawn('taskkill', ['/PID', String(processId), '/T', '/F'], {
                  shell: false,
                  stdio: 'ignore',
                  env: createRuntimeEnvironment(),
                })
                killer.once('error', () => child.kill(signal))
              } else process.kill(-processId, signal)
            } catch (error) {
              if ((error as NodeJS.ErrnoException).code !== 'ESRCH')
                console.error('[MaigoKite] Runtime shutdown:', error)
            }
          }
          const timeout = setTimeout(() => terminate(pid, 'SIGKILL'), 15_000)
          child.once('close', () => {
            clearTimeout(timeout)
            resolve()
          })
          // EOF works on Windows too; SIGTERM there would immediately terminate Python.
          child.stdin?.end()
          if (process.platform !== 'win32') terminate(pid, 'SIGTERM')
        }),
    ),
  ).then(() => undefined)
  return runtimeShutdown
}
export function isAllowedNavigation(target: string, development: boolean) {
  try {
    const url = new URL(target)
    if (url.protocol === 'file:') return path.resolve(fileURLToPath(url)) === indexPath
    return development && url.origin === developmentURL
  } catch {
    return false
  }
}
function secureNavigation(window: BrowserWindow) {
  window.webContents.setWindowOpenHandler(({ url }) => {
    try {
      if (['https:', 'http:'].includes(new URL(url).protocol)) void shell.openExternal(url)
    } catch {
      /* Invalid links are ignored. */
    }
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    if (!isAllowedNavigation(url, !builtUI)) event.preventDefault()
  })
}
export function createWindow() {
  const window = new BrowserWindow({
    title: 'MaigoKite',
    icon: appIconPath,
    width: 1320,
    height: 860,
    minWidth: 560,
    minHeight: 500,
    frame: false,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'hidden',
    trafficLightPosition: { x: 12, y: 11 },
    webPreferences: secureWebPreferences(),
  })
  secureNavigation(window)
  if (!builtUI) void window.loadURL(developmentURL)
  else void window.loadFile(indexPath)
  window.on('close', (event) => {
    if (!quitting) {
      event.preventDefault()
      window.hide()
    }
  })
  window.on('closed', () => {
    mainWindow = null
  })
  mainWindow = window
  return window
}
function showCapsule() {
  if (!capsuleWindow) {
    const window = new BrowserWindow({
      title: `MaigoKite · ${t('速记')}`,
      icon: appIconPath,
      width: 560,
      height: 56,
      frame: false,
      alwaysOnTop: true,
      resizable: false,
      skipTaskbar: true,
      show: false,
      webPreferences: secureWebPreferences(),
    })
    capsuleWindow = window
    secureNavigation(window)
    if (builtUI) void window.loadFile(indexPath, { hash: '/capsule' })
    else void window.loadURL(`${developmentURL}/capsule`)
    window.once('ready-to-show', () => {
      window.show()
      window.focus()
    })
    window.on('blur', () => window.hide())
    window.on('closed', () => {
      capsuleWindow = null
    })
  }
  const bounds = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
  capsuleWindow.setPosition(
    Math.round(bounds.x + (bounds.width - 560) / 2),
    Math.round(bounds.y + bounds.height * 0.2),
  )
  capsuleWindow.show()
  capsuleWindow.focus()
}
function setShortcut(value: string) {
  if (shortcut === value) return
  if (!globalShortcut.register(value, showCapsule))
    throw new Error(t('快捷键已被占用，请换一个组合。'))
  if (shortcut) globalShortcut.unregister(shortcut)
  shortcut = value
}
function setAppearance(value: Appearance) {
  if (appearance && appearance.capsuleShortcut !== value.capsuleShortcut)
    setShortcut(value.capsuleShortcut)
  else if (!shortcut) {
    try {
      setShortcut(value.capsuleShortcut)
    } catch {
      /* A reserved default shortcut must not block theme sync. */
    }
  }
  if (JSON.stringify(value) === JSON.stringify(appearance)) return
  // A failed disk write must not appear saved in either window or the next request.
  writeFileSync(appearancePath, JSON.stringify(value), 'utf8')
  appearance = value
  i18n.global.locale.value = resolveLanguage(value.language, app.getLocale())
  updateTrayMenu()
  setPulse(pulseState)
  capsuleWindow?.setTitle(`MaigoKite · ${t('速记')}`)
  for (const window of [mainWindow, capsuleWindow])
    window?.webContents.send('appearance:changed', value)
  publishGraphicsStatus()
}
function setPulse(state: PulseState) {
  pulseState = state
  tray?.setImage(trayImage(state))
  tray?.setToolTip(
    'MaigoKite · ' +
      t(
        {
          idle: '静息',
          thinking: '思考',
          acting: '行动',
          waiting: '等待确认',
          error: '出错',
          offline: '离线',
        }[state],
      ),
  )
}
function createTray() {
  tray = new Tray(trayImage('idle'))
  tray.setToolTip('MaigoKite')
  tray.on('click', () => {
    mainWindow?.show()
    mainWindow?.focus()
  })
  updateTrayMenu()
  setPulse(pulseState)
}
function updateTrayMenu() {
  tray?.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: t('显示窗口'),
        click: () => {
          mainWindow?.show()
          mainWindow?.focus()
        },
      },
      { label: t('速记'), click: showCapsule },
      { type: 'separator' },
      { label: t('退出'), click: () => app.quit() },
    ]),
  )
}
app
  .whenReady()
  .then(async () => {
    if (!singleInstance || quitting) return
    if (process.platform === 'darwin') app.dock?.setIcon(appIconPath)
    i18n.global.locale.value = resolveLanguage(appearance?.language, app.getLocale())
    registerIPC({
      main: () => mainWindow,
      capsule: () => capsuleWindow,
      allowed: (url) => isAllowedNavigation(url, !builtUI),
      workspace: (folderId = '') => registeredWorkspaceRoot(folderId),
      appearance: () => appearance,
      setAppearance,
      pulse: setPulse,
      graphics: graphicsStatus,
    })
    await startRuntime()
    if (!builtUI) await waitForService('Web')
    if (quitting) return
    createWindow()
    createTray()
    try {
      setShortcut(appearance?.capsuleShortcut || 'Alt+Space')
    } catch {
      /* Settings can choose a free shortcut. */
    }
    app.on('activate', () => {
      if (!mainWindow) createWindow()
      mainWindow?.show()
    })
  })
  .catch(async (error: unknown) => {
    reportRuntimeFailure(error instanceof Error ? error.message : String(error))
    await stopRuntime()
    if (quitting) app.quit()
    else app.exit(1)
  })
app.on('before-quit', (event) => {
  if (shutdownComplete || !singleInstance) return
  event.preventDefault()
  if (quitting) return
  quitting = true
  globalShortcut.unregisterAll()
  void stopRuntime().finally(() => {
    shutdownComplete = true
    app.quit()
  })
})
app.on('second-instance', () => {
  mainWindow?.show()
  mainWindow?.focus()
})
process.on('SIGINT', () => app.quit())
process.on('SIGTERM', () => app.quit())
process.on('SIGHUP', () => app.quit())
if (process.env.MAIGOKITE_PARENT_PIPE === '1') {
  process.stdin.on('end', () => app.quit())
  process.stdin.on('error', () => app.quit())
  process.stdin.resume()
}
