import { t } from '@/i18n'
export const apiBase =
  import.meta.env.VITE_API_BASE_URL || (location.protocol === 'file:' ? 'http://127.0.0.1:8080' : '')
export const workspacePath = '/api/v1/workspaces/personal'
export class GatewayError extends Error {
  constructor(
    message: string,
    public readonly status = 0,
    public readonly code = 'NETWORK',
  ) {
    super(message)
    this.name = 'GatewayError'
  }
}
let bearer = ''
export function setAuthToken(token: string) {
  bearer = token
}
export async function request<T>(
  url: string,
  init?: RequestInit,
  validate?: (value: unknown) => value is T,
): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  init?.signal?.addEventListener('abort', abort, { once: true })
  if (init?.signal?.aborted) abort()
  const timeout = setTimeout(abort, 30_000)
  const decode = (value: unknown): T => {
    if (validate) {
      if (!validate(value))
        throw new GatewayError(t('服务端数据格式异常，请更新应用或重试。'), 502, 'CONTRACT_INVALID')
      return value
    }
    // Endpoints not yet described by OpenAPI retain their existing boundary.
    // New/converted endpoints must supply the generated validator.
    return value as T
  }
  try {
    if (import.meta.env.VITE_MOCK === '1')
      return decode(await (await import('./mock')).mockRequest<unknown>(url, init))
    const headers = new Headers(init?.headers)
    headers.set('Content-Type', 'application/json')
    if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
    const response = await fetch(`${apiBase}${url}`, { ...init, headers, signal: controller.signal })
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null)
      const error = body && typeof body === 'object' ? (body as Record<string, unknown>).error : null
      const detail = error && typeof error === 'object' ? (error as Record<string, unknown>) : {}
      const message =
        typeof error === 'string'
          ? error
          : typeof detail.message === 'string'
            ? detail.message
            : response.status === 404
              ? t("此操作不可用，请重启或更新应用。")
              : t("请求失败（{0}）", [response.status])
      throw new GatewayError(message, response.status, String(detail.code || 'HTTP'))
    }
    const body: unknown = response.status === 204 ? undefined : await response.json()
    return decode(body)
  } catch (reason) {
    if (reason instanceof GatewayError) throw reason
    throw new GatewayError(
      controller.signal.aborted
        ? t("请求超时或已取消，请重试。")
        : reason instanceof Error
          ? reason.message
          : t("无法连接服务"),
    )
  } finally {
    clearTimeout(timeout)
    init?.signal?.removeEventListener('abort', abort)
  }
}
