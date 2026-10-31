import type { OnBeforeSendHeadersListenerDetails, Session } from 'electron'

export const backendURL = 'http://127.0.0.1:8080'
const authHeader = 'X-MaigoKite-Desktop-Token'

/** Send the secret only to the owned API, or its development proxy; never to redirects elsewhere. */
export function desktopHeaders(
  target: string,
  token: string,
  developmentURL?: string,
): Record<string, string> {
  const url = URL.parse(target)
  if (!token || !url || url.username || url.password) return {}
  if (url.protocol === 'ws:') url.protocol = 'http:'
  const apiTarget = url.origin === backendURL || url.origin === developmentURL
  const apiPath = url.pathname.startsWith('/api/v1/') || url.pathname === '/health'
  return apiTarget && apiPath ? { [authHeader]: token } : {}
}

export function authorizeDesktopRequests(
  session: Session,
  token: string,
  trustedFrame: (details: OnBeforeSendHeadersListenerDetails) => boolean,
  developmentURL?: string,
): void {
  // Run on every destination so a redirect cannot retain the credential.
  session.webRequest.onBeforeSendHeaders((details, callback) => {
    callback({
      requestHeaders: desktopRequestHeaders(details, token, trustedFrame(details), developmentURL),
    })
  })
}

export function desktopRequestHeaders(
  request: Pick<OnBeforeSendHeadersListenerDetails, 'url' | 'requestHeaders'>,
  token: string,
  trustedFrame: boolean,
  developmentURL?: string,
): Record<string, string> {
  const headers = { ...request.requestHeaders }
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === authHeader.toLowerCase()) delete headers[key]
  }
  if (trustedFrame) Object.assign(headers, desktopHeaders(request.url, token, developmentURL))
  return headers
}
