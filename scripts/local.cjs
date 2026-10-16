#!/usr/bin/env node
// Shared source-tree entry point. Bash/PowerShell wrappers only forward arguments.
// Process fan-out and failure propagation use concurrently, not a custom supervisor.
const { spawn, spawnSync } = require('node:child_process')
const { accessSync, constants, existsSync, readFileSync, statSync } = require('node:fs')
const { createRequire } = require('node:module')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { requireBuild } = require('./lib/web-build.cjs')

const root = path.resolve(__dirname, '..')
const web = path.join(root, 'web')
const windows = process.platform === 'win32'
const requireWeb = createRequire(path.join(web, 'package.json'))
const [action, targetArg, modeArg, ...extra] = process.argv.slice(2)

function run(command, args, cwd = root, env = process.env) {
  const result = spawnSync(command, args, { cwd, env, stdio: 'inherit', windowsHide: true })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${path.basename(command)} failed (${result.signal || result.status})`)
}

function output(command, args) {
  const result = spawnSync(command, args, {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 15_000, windowsHide: true,
  })
  return result.status === 0 ? result.stdout.trim() : ''
}

function needFile(file) {
  if (!existsSync(file)) throw new Error(`Missing ${file}. Run setup / build first.`)
  return file
}

function hostPath(value, base = root) {
  return path.resolve(base, value.replace(/^~(?=[/\\]|$)/, os.homedir()))
}

function environmentPython(directory) {
  return path.join(directory, windows ? 'Scripts/python.exe' : 'bin/python')
}

function projectEnvironment() {
  return hostPath(process.env.UV_PROJECT_ENVIRONMENT || '.venv')
}

function probePython(command, args = []) {
  return output(command, [...args, '-I', '-c',
    'import sys; sys.exit(1) if sys.version_info[:2] != (3, 14) else print(sys.executable)'])
}

function findPython() {
  const override = process.env.MAIGOKITE_PYTHON
  if (override) {
    const found = probePython(/[/\\]/.test(override) ? hostPath(override) : override)
    if (found) return found
    throw new Error('MAIGOKITE_PYTHON must identify a usable Python 3.14.x executable.')
  }
  const projectPython = environmentPython(projectEnvironment())
  if (existsSync(projectPython)) {
    const found = probePython(projectPython)
    if (found) return found
    throw new Error('Project Python environment is unavailable or not Python 3.14.x. Stop the app and run setup with uv; without uv, rename the old environment before setup.')
  }
  if (process.env.VIRTUAL_ENV) {
    const found = probePython(environmentPython(hostPath(process.env.VIRTUAL_ENV)))
    if (found) return found
  }
  const uvPython = output('uv', ['python', 'find', '3.14', '--no-python-downloads'])
  if (uvPython) {
    const found = probePython(uvPython)
    if (found) return found
  }
  for (const command of ['python3.14', 'python', 'python3']) {
    const found = probePython(command)
    if (found) return found
  }
  if (windows) {
    const found = probePython('py', ['-3.14'])
    if (found) return found
  }
  throw new Error('Python 3.14.x not found. Run setup with uv, install Python 3.14, or set MAIGOKITE_PYTHON.')
}

function npm(args, cwd = web) {
  if (!windows) return run('npm', args, cwd)
  // npm.cmd cannot be execFile'd. Invoke its installed JS entry with Node instead
  // of interpolating a repository path into cmd.exe (spaces, &, Unicode paths).
  const directories = [path.dirname(process.execPath), ...(process.env.PATH || '').split(path.delimiter)]
  for (const directory of directories) {
    const cli = path.join(directory.replace(/^"|"$/g, ''), 'node_modules/npm/bin/npm-cli.js')
    if (existsSync(cli)) return run(process.execPath, [cli, ...args], cwd)
  }
  throw new Error('npm CLI not found beside Node/on PATH. Install Node.js with npm.')
}

function needWebDependencies() {
  needFile(path.join(web, 'node_modules/vite/bin/vite.js'))
  needFile(path.join(web, 'node_modules/concurrently/package.json'))
}

function needElectron() {
  try {
    // Electron 42+ downloads on require('electron'). Startup must remain offline;
    // only setup invokes the official installer, then we read its installed path.
    const packagePath = requireWeb.resolve('electron/package.json')
    const directory = path.dirname(packagePath)
    const expected = requireWeb('electron/package.json').version
    const locked = JSON.parse(readFileSync(path.join(web, 'package-lock.json'), 'utf8'))
      .packages['node_modules/electron'].version
    if (expected !== locked) throw new Error(`Electron dependency is outdated: expected ${locked}, found ${expected}`)
    const platformPath = process.platform === 'darwin' ? 'Electron.app/Contents/MacOS/Electron'
      : windows ? 'electron.exe' : 'electron'
    const executable = process.env.ELECTRON_OVERRIDE_DIST_PATH
      ? path.join(process.env.ELECTRON_OVERRIDE_DIST_PATH, platformPath)
      : path.join(directory, 'dist', readFileSync(path.join(directory, 'path.txt'), 'utf8').trim())
    const info = statSync(executable)
    if (!info.isFile() || !info.size) throw new Error(`Not a non-empty executable: ${executable}`)
    accessSync(executable, constants.X_OK)
    // Custom distributions are explicitly owned by the caller. For the npm
    // installation, also reject stale binaries left over from another version.
    if (!process.env.ELECTRON_OVERRIDE_DIST_PATH) {
      const installed = readFileSync(path.join(directory, 'dist/version'), 'utf8').trim().replace(/^v/, '')
      if (installed !== expected) throw new Error(`Electron version mismatch: expected ${expected}, found ${installed}`)
    }
    return executable
  } catch (error) {
    throw new Error([
      `Electron runtime is missing or incomplete (Node ${process.version}).`,
      `Cause: ${error.message}`,
      'Repair from the project root (no Python reinstall):',
      windows ? '.\\scripts\\setup.ps1 electron' : 'bash scripts/setup.sh electron',
      'Rebuilding application code will not install the Electron runtime.',
      'See docs/development/DevelopmentGuide.md (Electron installation).',
    ].join('\n'))
  }
}

function checkElectronInstallEnvironment() {
  for (const [key, host] of [
    ['ELECTRON_INSTALL_PLATFORM', process.platform], ['ELECTRON_INSTALL_ARCH', process.arch],
    ['npm_config_platform', process.platform], ['npm_config_arch', process.arch],
  ]) {
    if (process.env[key] && process.env[key] !== host)
      throw new Error(`Unset ${key}: local desktop dependencies must match this computer (${host}).`)
  }
}

async function setup(target) {
  if (!['all', 'dev', 'python', 'electron', 'sandbox'].includes(target) || modeArg || extra.length)
    throw new Error('Usage: setup [all|dev|python|electron|sandbox]')
  // uv may replace an incompatible venv; npm ci replaces node_modules.
  // Refuse while the standard suite ports are occupied instead of killing it.
  await freePorts([8080, 5173])
  if (target === 'sandbox') {
    if (!windows) throw new Error('The sandbox dependency target is for native Windows only; Linux uses bubblewrap.')
    npm(['ci', '--ignore-scripts', '--engine-strict', '--no-audit', '--no-fund'], path.join(root, 'scripts/windows-sandbox'))
    console.info('Windows sandbox dependency installed. No accounts, ACLs or firewall rules were changed.')
    console.info('Read docs/user-guide/Configuration.md before explicitly running scripts/sandbox.ps1 install -ConfirmSystemChanges.')
    return
  }
  if (target !== 'python') checkElectronInstallEnvironment()
  if (target === 'electron') {
    // Restore the locked dependency tree, then let Electron own download and
    // checksum verification. The Python environment is not involved.
    console.info('[MaigoKite] Repairing Electron: refreshing Web dependencies, then running its official installer.')
    npm(['ci', '--ignore-scripts', '--include=dev', '--engine-strict', '--no-audit', '--no-fund'])
    npm(['run', 'electron:install'])
    console.info(`[MaigoKite] Electron ready: ${needElectron()}`)
    return
  }
  const development = target === 'dev'
  const requestedPython = process.env.MAIGOKITE_PYTHON ? findPython() : '3.14'
  console.info(`[MaigoKite] Syncing Python 3.14 ${development ? 'runtime and development' : 'runtime'} dependencies in ${projectEnvironment()}.`)
  if (output('uv', ['--version'])) {
    // Exact sync removes unselected packages, including old dev-only tools.
    // uv owns environment replacement; shared interpreters and caches stay intact.
    const args = ['sync', '--locked', '--no-default-groups', '--python', requestedPython]
    if (development) args.push('--extra', 'dev')
    run('uv', args)
  } else {
    console.warn('[MaigoKite] uv is unavailable: pip installs the declared dependencies without lockfile enforcement or pruning old packages. Use uv for an exact environment cleanup.')
    const venvPython = environmentPython(projectEnvironment())
    if (!existsSync(venvPython)) run(findPython(), ['-m', 'venv', projectEnvironment()])
    if (!probePython(venvPython)) throw new Error('Project environment requires Python 3.14.x. Stop the app, rename the old environment, then rerun setup with Python 3.14 installed.')
    run(venvPython, ['-m', 'pip', 'install', '-e', development ? '.[dev]' : '.'])
  }
  if (target !== 'python') {
    // Vite, TypeScript and Electron are needed to build/run the source checkout;
    // npm's devDependencies are separate from the optional Python dev tools.
    npm(['ci', '--include=dev', '--engine-strict', '--foreground-scripts', '--ignore-scripts=false'])
    npm(['run', 'electron:install'])
    console.info(`[MaigoKite] Electron ready: ${needElectron()}`)
    if (windows) {
      npm(['ci', '--ignore-scripts', '--engine-strict', '--no-audit', '--no-fund'], path.join(root, 'scripts/windows-sandbox'))
      console.info('Windows sandbox dependency installed; system provisioning remains a separate explicit step.')
    }
  }
  console.info(target === 'python' ? 'Python dependencies installed; Web/Electron dependencies unchanged.' : 'Application dependencies installed.')
  console.info('Browser tools additionally need: <Core Python> -m playwright install chromium')
}

function build(target) {
  if (modeArg || extra.length || !['web', 'desktop', 'all', 'python', 'vue'].includes(target))
    throw new Error('Usage: build [web|desktop|all|python|vue]')
  if (target === 'python') {
    const out = path.join(root, 'dist')
    if (output('uv', ['--version'])) {
      const args = ['build', '--out-dir', out, '--python',
        process.env.MAIGOKITE_PYTHON ? findPython() : '3.14']
      run('uv', args)
    } else {
      const python = findPython()
      if (!output(python, ['-I', '-c', 'import build; print("ready")']))
        throw new Error('Building a Python distribution requires uv or the optional build tool. Install uv or run setup dev first.')
      run(python, ['-m', 'build', '--outdir', out])
    }
    return
  }
  needWebDependencies()
  if (['desktop', 'all'].includes(target)) needElectron()
  npm(['run', ['desktop', 'all'].includes(target) ? 'build' : 'build:web'])
  console.info('Build complete. Source-tree output only; Python dependencies are still required (not an installer).')
}

async function freePorts(ports) {
  for (const port of ports) {
    await new Promise((resolve, reject) => {
      const server = net.createServer()
      server.once('error', () => reject(new Error(`Port ${port} unavailable. Exit the existing suite first; no process was killed.`)))
      server.listen({ host: '127.0.0.1', port, exclusive: true }, () => server.close(resolve))
    })
  }
}

async function start(target, mode) {
  if (extra.length || !['web', 'desktop'].includes(target) || !['dev', 'built'].includes(mode))
    throw new Error('Usage: start [web|desktop] [dev|built]')
  if (mode === 'built') requireBuild(target)
  needWebDependencies()
  const electron = target === 'desktop' ? needElectron() : undefined
  // These are real, owned suites; never silently attach to an old Core or use mock UI.
  if (process.env.MAIGOKITE_EXTERNAL_RUNTIME === '1' || process.env.VITE_MOCK === '1')
    throw new Error('Unset MAIGOKITE_EXTERNAL_RUNTIME / VITE_MOCK before starting a complete suite.')
  const python = findPython()
  const config = needFile(hostPath(process.env.MAIGOKITE_CONFIG || 'config/config.dev.yaml'))
  const env = {
    ...process.env, MAIGOKITE_PROJECT_ROOT: root, MAIGOKITE_CONFIG: config,
    MAIGOKITE_PYTHON: python, PYTHONUNBUFFERED: '1', PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
    MAIGOKITE_INSTALL_DIR: hostPath(process.env.MAIGOKITE_INSTALL_DIR || root),
    MAIGOKITE_BACKEND_PROXY_TARGET: 'http://127.0.0.1:8080',
    MAIGOKITE_PARENT_PIPE: '1',
  }
  if (env.MAIGOKITE_WORKSPACE_ROOT) env.MAIGOKITE_WORKSPACE_ROOT = hostPath(env.MAIGOKITE_WORKSPACE_ROOT)
  const port = mode === 'dev' ? 5173 : 8080
  await freePorts([8080, ...(mode === 'dev' ? [5173] : [])])
  if (mode === 'dev') {
    if (target === 'desktop') npm(['run', 'electron:compile'])
  }
  env.MAIGOKITE_DESKTOP_BUILT = mode === 'built' ? '1' : '0'
  const vite = path.join(web, 'node_modules/vite/bin/vite.js')
  const specs = target === 'web'
    ? [
        { name: 'Backend', executable: python,
          args: [path.join(root, 'python/main.py'), 'run', '--config', config, '--port', '8080',
            ...(mode === 'built' ? ['--web-dir', path.join(web, 'dist')] : [])], cwd: root },
      ]
    : [{ name: 'Desktop', executable: electron, args: [web], cwd: root }]
  if (mode === 'dev') specs.push({
    name: 'Web', executable: process.execPath,
    args: ['--require', path.join(__dirname, 'lib/web-owner.cjs'), vite,
      '--host', '127.0.0.1', '--port', String(port), '--strictPort'], cwd: web,
  })

  const { concurrently, Logger, LogOutput, LogError, LogExit, KillOnSignal, KillOthers } = requireWeb('concurrently')
  const treeKill = requireWeb('tree-kill')
  const logger = new Logger({ prefixFormat: '[{name}]' })
  const owned = new Map()
  const { result } = concurrently(specs.map(({ name, cwd }) => ({ name, command: name, cwd, env })), {
    logger, outputStream: process.stdout, successCondition: 'first',
    controllers: [new LogOutput({ logger }), new LogError({ logger }), new LogExit({ logger }),
      new KillOnSignal({ process }), new KillOthers({ logger, conditions: ['success', 'failure'] })],
    spawn(name, options) {
      const spec = specs.find((item) => item.name === name)
      const child = spawn(spec.executable, spec.args, {
        ...options, shell: false, windowsHide: true, detached: !windows, stdio: ['pipe', 'pipe', 'pipe'],
      })
      const record = { child, name, timer: null }
      if (child.pid) owned.set(child.pid, record)
      child.stdin.on('error', () => {}) // EPIPE when a child fails before shutdown.
      child.once('close', () => { clearTimeout(record.timer); owned.delete(child.pid) })
      return child
    },
    kill(pid) {
      const record = owned.get(pid)
      if (!record || record.timer) return
      // Backend/Desktop treat EOF as graceful stop, including owner crashes.
      // Windows cannot use SIGTERM for graceful cleanup. Force only after the wait.
      record.child.stdin.end()
      const force = () => {
        if (windows) treeKill(pid, 'SIGKILL', (error) => {
          if (error && owned.has(pid)) console.error(`Unable to stop ${record.name}: ${error.message}`)
        })
        else {
          try { process.kill(-pid, 'SIGKILL') } catch (error) {
            if (error.code !== 'ESRCH') console.error(`Unable to stop ${record.name}: ${error.message}`)
          }
        }
      }
      record.timer = setTimeout(force, record.name === 'Desktop' ? 20_000 : 15_000)
    },
  })
  if (target === 'web') console.info(`Starting local Web suite: http://127.0.0.1:${port} (wait for services to report ready).`)
  console.info('Ctrl+C stops the whole suite. Desktop window close hides to tray; use tray Exit to quit.')
  try { await result } catch { throw new Error('Suite stopped after a component failed; see the component log above.') }
}

async function main() {
  // Match web/package.json engines (including the locked nopt/abbrev tools).
  const [major, minor, patch] = process.versions.node.split('.').map(Number)
  if (!((major === 22 && (minor > 22 || (minor === 22 && patch >= 2))) ||
        (major === 24 && minor >= 15) || major >= 26))
    throw new Error(`Node.js 22.22.2+ (22.x), 24.15.0+ (24.x), or 26+ is required; found ${process.version}.`)
  if (action === 'setup') await setup(targetArg || 'all')
  else if (action === 'build') build(targetArg || 'all')
  else if (action === 'start') await start(targetArg || 'web', modeArg || 'dev')
  else throw new Error('Usage: node scripts/local.cjs setup [all|dev|python|electron] | build [web|desktop|all|python|vue] | start [web|desktop] [dev|built]')
}
main().catch((error) => { console.error(`[MaigoKite] ${error.message}`); process.exitCode = 1 })
