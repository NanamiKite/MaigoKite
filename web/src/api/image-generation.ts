import { request, workspacePath } from './http'
import type { components } from './generated/transport'
import { validateImageSettingsResponse } from './generated/validators.js'

export type ImageGenerationSettings = components['schemas']['ImageSettings']
export function getImageSettings(signal?: AbortSignal) {
  return request(
    `${workspacePath}/settings/image-generation`,
    { signal },
    validateImageSettingsResponse,
  )
}
export function saveImageSettings(
  settings: ImageGenerationSettings,
  apiKey: string,
  clear: boolean,
) {
  return request(
    `${workspacePath}/settings/image-generation`,
    {
      method: 'PUT',
      body: JSON.stringify({ settings, api_key: apiKey, clear_api_key: clear }),
    },
    validateImageSettingsResponse,
  )
}
