// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { desktopHeaders, desktopRequestHeaders } from '@/electron/desktop-access'

const token = 'a'.repeat(64)
const header = 'X-MaigoKite-Desktop-Token'
const api = 'http://127.0.0.1:8080/api/v1/workspaces/personal/conversations'
const development = 'http://127.0.0.1:5173'

describe('desktop API credentials', () => {
  it.each([api, api.replace('http:', 'ws:'), 'http://127.0.0.1:8080/health'])(
    'authenticates the owned API target %s',
    (url) => expect(desktopHeaders(url, token)).toEqual({ [header]: token }),
  )

  it.each([
    'http://127.0.0.1:8081/api/v1/workspaces/personal/conversations',
    'http://127.0.0.1.evil.test:8080/api/v1/workspaces/personal/conversations',
    'https://example.com/api/v1/workspaces/personal/conversations',
    'http://user:password@127.0.0.1:8080/api/v1/workspaces/personal/conversations',
    'http://127.0.0.1:8080/echo',
    'file:///tmp/other.html',
    'not a URL',
  ])('does not disclose the credential to %s', (url) => {
    expect(desktopHeaders(url, token)).toEqual({})
  })

  it('permits only API paths of the explicitly enabled development proxy', () => {
    const url = api.replace('8080', '5173')
    expect(desktopHeaders(url, token)).toEqual({})
    expect(desktopHeaders(url, token, development)).toEqual({ [header]: token })
    expect(desktopHeaders(development, token, development)).toEqual({})
    expect(desktopHeaders(api, '')).toEqual({})
  })

  it('strips supplied credentials from foreign frames and redirected destinations', () => {
    const requestHeaders = {
      [header]: token,
      [header.toLowerCase()]: 'forged',
      Accept: 'application/json',
    }
    expect(desktopRequestHeaders({ url: api, requestHeaders }, token, false)).toEqual({
      Accept: 'application/json',
    })
    expect(
      desktopRequestHeaders({ url: 'https://evil.test/', requestHeaders }, token, true),
    ).toEqual({ Accept: 'application/json' })
    expect(desktopRequestHeaders({ url: api, requestHeaders }, token, true)).toEqual({
      Accept: 'application/json',
      [header]: token,
    })
    expect(requestHeaders[header.toLowerCase()]).toBe('forged')
  })
})
