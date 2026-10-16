const { randomUUID } = require('node:crypto')
const {
  accessSync,
  constants,
  lstatSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const marker = '# Managed by MaigoKite source launcher\n'

function desktopValue(value) {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t')
}

function execArgument(value) {
  // Desktop entries apply string escaping before Exec quoting and percent expansion.
  return desktopValue(`"${value.replace(/%/g, '%%').replace(/["`$\\]/g, '\\$&')}"`)
}

function registerSourceDesktop(root, mode) {
  const web = path.join(root, 'web')
  const metadata = JSON.parse(readFileSync(path.join(web, 'package.json'), 'utf8'))
  if (
    typeof metadata.desktopName !== 'string' ||
    !/^[a-zA-Z0-9.-]+\.desktop$/.test(metadata.desktopName)
  )
    throw new Error('package.json requires a valid desktopName.')
  if (!['dev', 'built'].includes(mode)) throw new Error('Invalid desktop launch mode.')
  if (process.execPath.includes('='))
    throw new Error('Desktop Exec cannot use a Node path containing =.')
  const id = metadata.desktopName.replace(/\.desktop$/, '.source')
  const filename = `${id}.desktop`
  const configuredHome = process.env.XDG_DATA_HOME
  const dataHome =
    configuredHome && path.isAbsolute(configuredHome)
      ? configuredHome
      : path.join(os.homedir(), '.local', 'share')
  const directory = path.join(dataHome, 'applications')
  const destination = path.join(directory, filename)
  const icon = path.join(web, metadata.build.icon)
  accessSync(icon, constants.R_OK)
  const command = [process.execPath, path.join(root, 'scripts/local.cjs'), 'start', 'desktop', mode]
    .map(execArgument)
    .join(' ')
  const content =
    marker +
    [
      '[Desktop Entry]',
      'Type=Application',
      'Name=MaigoKite',
      `Exec=${command}`,
      `Path=${desktopValue(root)}`,
      `Icon=${desktopValue(icon)}`,
      `StartupWMClass=${id}`,
      'Terminal=false',
      // Associate running windows without adding a source checkout to the app menu.
      'NoDisplay=true',
      '',
    ].join('\n')
  try {
    if (!lstatSync(destination).isFile()) throw new Error(`Refusing to replace ${destination}.`)
    const previous = readFileSync(destination, 'utf8')
    if (!previous.startsWith(marker)) throw new Error(`Unmanaged desktop entry: ${destination}.`)
    if (previous === content) return filename
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  mkdirSync(directory, { recursive: true })
  // Rename prevents GNOME from observing a partial entry and never follows a target symlink.
  const temporary = path.join(directory, `.${filename}.${randomUUID()}.tmp`)
  try {
    writeFileSync(temporary, content, { encoding: 'utf8', mode: 0o644, flag: 'wx' })
    renameSync(temporary, destination)
  } finally {
    try {
      unlinkSync(temporary)
    } catch (error) {
      if (error.code !== 'ENOENT')
        console.warn(`[MaigoKite] Desktop entry temporary file cleanup failed: ${error.message}`)
    }
  }
  return filename
}

module.exports = { registerSourceDesktop }
