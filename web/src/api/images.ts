import { request, workspacePath } from './http'
import type { components } from './generated/transport'
import { validateImageReference } from './generated/validators.js'

export type ImageReference = components['schemas']['ImageReference']
export function parseImages(source: string): ImageReference[] {
  try {
    const value: unknown = JSON.parse(source)
    const images = Array.isArray(value)
      ? value
      : value && typeof value === 'object' && 'images' in value
        ? value.images
        : value &&
            typeof value === 'object' &&
            'data' in value &&
            value.data &&
            typeof value.data === 'object' &&
            'images' in value.data
          ? value.data.images
          : []
    return Array.isArray(images) ? images.filter(validateImageReference).slice(0, 4) : []
  } catch {
    return []
  }
}
export function uploadImage(data: string, folderId: string, signal?: AbortSignal) {
  return request(
    `${workspacePath}/files/images?folder_id=${encodeURIComponent(folderId)}`,
    {
      method: 'POST',
      body: JSON.stringify({ data }),
      signal,
    },
    validateImageReference,
  )
}
