import type { Step, Turn } from '@/api/events'

export function stepArguments(step: Step): Record<string, unknown> {
  return step.input && typeof step.input === 'object' && !Array.isArray(step.input)
    ? (step.input as Record<string, unknown>)
    : {}
}

export function commandSteps(turn: Turn): Step[] {
  return turn.steps.filter((step) =>
    ['bash', 'exec_command', 'shell', 'powershell'].includes(step.tool),
  )
}

export function commandText(step: Step): string {
  const input = stepArguments(step)
  const command = input.command ?? input.cmd
  if (typeof command === 'string') return command
  if (Array.isArray(input.argv) && input.argv.every((value) => typeof value === 'string'))
    return JSON.stringify(input.argv)
  return ''
}

/** Normalize only against the recorded workspace; never strip an arbitrary absolute prefix. */
export function workspaceRelativePath(path: string, root = ''): string | null {
  let relative = path.replace(/\\/g, '/')
  const base = root.replace(/\\/g, '/').replace(/\/+$/, '')
  const absolute = relative.startsWith('/') || /^[a-z]:\//i.test(relative)
  if (absolute) {
    if (!base && root !== '/') return null
    const prefix = base + '/'
    const matches = /^[a-z]:/i.test(base)
      ? relative.toLowerCase().startsWith(prefix.toLowerCase())
      : relative.startsWith(prefix)
    if (!matches) return null
    relative = relative.slice(prefix.length)
  }
  const parts = relative.split('/').filter((part) => part && part !== '.')
  if (!parts.length || parts.includes('..') || parts.some((part) => part.toLowerCase() === '.git'))
    return null
  return parts.join('/')
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/** Read only the host's imported-file projection, never raw MCP content/resources. */
function antennaFiles(step: Step, folderId: string | null): string[] {
  if (folderId === null || !['completed', 'failed'].includes(step.status)) return []
  const action = step.tool.match(
    /(?:^|__)antenna[._](solve|get_result|render|export|get_delivery)$/,
  )?.[1]
  if (!action) return []
  try {
    const value = record(JSON.parse(step.output))
    const data = record(value?.data) ?? value
    const delivery = action === 'export' || action === 'get_delivery'
    const receipt = record(data?.[delivery ? 'antenna_delivery' : 'antenna_result'])
    const identity = delivery ? receipt?.delivery_id : receipt?.run_id
    const directory = data?.output_directory
    if (
      !data ||
      receipt?.schema_version !== 1 ||
      typeof identity !== 'string' ||
      !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(identity) ||
      (delivery
        ? receipt.kind !== 'antenna_design_delivery' ||
          receipt.status !== 'CANDIDATE_NOT_VALIDATED' ||
          typeof receipt.design_sha256 !== 'string' ||
          !/^[a-f0-9]{64}$/.test(receipt.design_sha256)
        : record(receipt.summary)?.status !== 'COMPUTED_NOT_VALIDATED') ||
      typeof data.import_complete !== 'boolean' ||
      typeof directory !== 'string' ||
      !/^antenna-results\/[a-f0-9]{32}$/.test(directory) ||
      !Array.isArray(data.files) ||
      'content' in data ||
      'structuredContent' in data ||
      'result' in data
    )
      return []
    return data.files.slice(0, 32).flatMap((value: unknown) => {
      const file = record(value)
      if (
        !file ||
        file.folder_id !== folderId ||
        typeof file.sha256 !== 'string' ||
        !/^[a-f0-9]{64}$/.test(file.sha256) ||
        typeof file.path !== 'string' ||
        !file.path.startsWith(directory + '/') ||
        file.path.slice(directory.length + 1).includes('/') ||
        Array.from(file.path).some((character) => {
          const code = character.charCodeAt(0)
          return code < 32 || code === 127
        }) ||
        workspaceRelativePath(file.path) !== file.path
      )
        return []
      return [file.path]
    })
  } catch {
    return []
  }
}

export function changedFiles(
  turn: Turn,
  root = '',
  folderId: string | null = null,
): { path: string; relative: string | null }[] {
  const paths = turn.changes.map((change) => change.path)
  for (const step of turn.steps) {
    paths.push(...antennaFiles(step, folderId))
    if (
      step.status !== 'completed' ||
      !['file_write', 'file_edit', 'write', 'edit'].includes(step.tool)
    )
      continue
    const path = stepArguments(step).path
    if (typeof path === 'string' && path) paths.push(path)
  }
  const seen = new Set<string>()
  return paths.flatMap((path) => {
    const relative = workspaceRelativePath(path, root)
    const key = relative ?? path
    if (seen.has(key)) return []
    seen.add(key)
    return [{ path: relative ?? path, relative }]
  })
}
