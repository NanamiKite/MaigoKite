import type { ExtensionConfig, MCPConfiguration, ManagedSkill } from '@/api/client'

export const EXTENSION_BUNDLE_MAX_BYTES = 1024 * 1024

export interface ExtensionBundle {
  format: 'maigokite-extension-bundle'
  version: 1
  name: string
  mcp_servers: MCPConfiguration[]
  skills: ManagedSkill[]
}

function invalid(): never {
  throw new Error('工作流文件格式无效或包含不支持的字段。')
}

function fields(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  if (Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key)))
    return invalid()
  return value as Record<string, unknown>
}

function string(value: unknown, maximum: number, minimum = 0): string {
  if (
    typeof value !== 'string' ||
    value.length < minimum ||
    value.length > maximum ||
    value.includes('\0')
  )
    return invalid()
  return value
}

function boolean(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalid()
}

function strings(value: unknown, maximum: number, itemMaximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) return invalid()
  return value.map((item: unknown) => string(item, itemMaximum))
}

function server(value: unknown): MCPConfiguration {
  const row = fields(value, [
    'namespace',
    'transport',
    'command',
    'args',
    'cwd',
    'enabled',
    'env_allowlist',
    'secret_env',
    'enabled_tools',
    'disabled_tools',
    'result_adapter',
    'request_timeout_seconds',
  ])
  const namespace = string(row.namespace, 48, 1)
  const command = string(row.command, 4096, 1)
  if (
    !/^[a-z][a-z0-9_-]{0,47}$/.test(namespace) ||
    namespace.includes('__') ||
    row.transport !== 'stdio' ||
    !command.trim() ||
    /[\r\n]/.test(command)
  )
    return invalid()
  const envAllowlist = strings(row.env_allowlist, 64, 128)
  const secretEnv = strings(row.secret_env, 64, 128)
  if (
    [...envAllowlist, ...secretEnv].some((name) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) ||
    secretEnv.some((name) => envAllowlist.includes(name))
  )
    return invalid()
  const timeout = row.request_timeout_seconds
  if (typeof timeout !== 'number' || !Number.isInteger(timeout) || timeout < 1 || timeout > 660)
    return invalid()
  if (row.result_adapter !== 'none' && row.result_adapter !== 'antenna_design') return invalid()
  return {
    namespace,
    transport: 'stdio',
    command,
    args: strings(row.args, 128, 8192),
    cwd: string(row.cwd, 4096),
    enabled: boolean(row.enabled),
    env_allowlist: envAllowlist,
    secret_env: secretEnv,
    enabled_tools: row.enabled_tools === null ? null : strings(row.enabled_tools, 256, 256),
    disabled_tools: strings(row.disabled_tools, 256, 256),
    result_adapter: row.result_adapter,
    request_timeout_seconds: timeout,
  }
}

function skill(value: unknown): ManagedSkill {
  const row = fields(value, ['name', 'content', 'enabled'])
  const name = string(row.name, 64, 1)
  if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(name)) return invalid()
  return {
    name,
    content: string(row.content, 256 * 1024, 1),
    enabled: boolean(row.enabled),
  }
}

/** Local data only: no commands, Markdown, credentials, or network requests are executed. */
export function parseExtensionBundle(text: string): ExtensionBundle {
  if (new TextEncoder().encode(text).byteLength > EXTENSION_BUNDLE_MAX_BYTES)
    throw new Error('工作流文件不能超过 1 MiB。')
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    return invalid()
  }
  const row = fields(value, ['format', 'version', 'name', 'mcp_servers', 'skills'])
  if (
    row.format !== 'maigokite-extension-bundle' ||
    row.version !== 1 ||
    !Array.isArray(row.mcp_servers) ||
    row.mcp_servers.length > 32 ||
    !Array.isArray(row.skills) ||
    row.skills.length > 128 ||
    (!row.mcp_servers.length && !row.skills.length)
  )
    return invalid()
  const servers = row.mcp_servers.map(server)
  const skills = row.skills.map(skill)
  const prefixes = servers.map((item) => item.namespace.replace(/_+$/, ''))
  if (
    new Set(prefixes).size !== servers.length ||
    new Set(skills.map((item) => item.name)).size !== skills.length
  )
    return invalid()
  return {
    format: 'maigokite-extension-bundle',
    version: 1,
    name: string(row.name, 128, 1),
    mcp_servers: servers,
    skills,
  }
}

/** Mutate only the draft clone owned by useExtensions.save, preserving all other settings. */
export function mergeExtensionBundle(config: ExtensionConfig, bundle: ExtensionBundle): void {
  for (const item of bundle.mcp_servers) {
    const index = config.mcp_servers.findIndex((row) => row.namespace === item.namespace)
    if (index < 0) config.mcp_servers.push(item)
    else config.mcp_servers[index] = item
  }
  for (const item of bundle.skills) {
    const index = config.skills.findIndex((row) => row.name === item.name)
    if (index < 0) config.skills.push(item)
    else config.skills[index] = item
  }
  const prefixes = config.mcp_servers.map((item) => item.namespace.replace(/_+$/, ''))
  if (
    new Set(prefixes).size !== prefixes.length ||
    prefixes.length > 32 ||
    config.skills.length > 128
  )
    throw new Error('合并后的扩展数量超限或服务名称冲突。')
}
