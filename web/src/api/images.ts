import { request, requestBlob, workspacePath } from './http'
import type { components } from './generated/transport'
import {
  validateImageReference,
  validateTextReference,
  validateDocumentReference,
  validateDocumentPage,
  validateDocumentNavigation,
  validateInputCapabilities,
} from './generated/validators.js'

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

export type TextReference = components['schemas']['TextReference']
export type DocumentReference = components['schemas']['DocumentReference']
export type DocumentPage = components['schemas']['DocumentPage']
export type DocumentNavigation = components['schemas']['DocumentNavigation']
export type AttachmentReference = ImageReference | TextReference | DocumentReference
export type InputCapabilities = components['schemas']['InputCapabilities']
function validateAttachment(value: unknown): value is AttachmentReference {
  return (
    validateImageReference(value) ||
    validateTextReference(value) ||
    validateDocumentReference(value)
  )
}
export function parseAttachments(source: string): AttachmentReference[] {
  try {
    const value: unknown = JSON.parse(source)
    return Array.isArray(value) ? value.filter(validateAttachment).slice(0, 8) : []
  } catch {
    return []
  }
}
export function uploadAttachment(
  name: string,
  data: string,
  folderId: string,
  signal?: AbortSignal,
) {
  return request(
    `${workspacePath}/files/attachments?folder_id=${encodeURIComponent(folderId)}`,
    {
      method: 'POST',
      body: JSON.stringify({ name, data }),
      signal,
    },
    validateAttachment,
  )
}
export function referenceAttachment(path: string, folderId: string, signal?: AbortSignal) {
  return request(
    `${workspacePath}/files/attachments/reference?folder_id=${encodeURIComponent(folderId)}`,
    {
      method: 'POST',
      body: JSON.stringify({ path }),
      signal,
    },
    validateAttachment,
  )
}
export function getInputCapabilities(modelId: string, signal?: AbortSignal) {
  return request(
    `${workspacePath}/settings/model-inputs?model_id=${encodeURIComponent(modelId)}`,
    { signal },
    validateInputCapabilities,
  )
}

export function readDocument(
  path: string,
  folderId: string,
  unit: number,
  textOffset: number,
  sha256: string,
  signal: AbortSignal,
  mode: 'text_layer' | 'ocr' = 'text_layer',
) {
  const query = new URLSearchParams({
    path,
    folder_id: folderId,
    unit: String(unit),
    mode,
    text_offset: String(textOffset),
    sha256,
  })
  return request(`${workspacePath}/files/document?${query}`, { signal }, validateDocumentPage)
}

export function navigateDocument(
  path: string,
  folderId: string,
  sha256: string,
  action: 'outline' | 'search',
  query: string,
  offset: number,
  signal: AbortSignal,
) {
  const parameters = new URLSearchParams({
    path,
    folder_id: folderId,
    sha256,
    action,
    query,
    offset: String(offset),
  })
  return request(
    `${workspacePath}/files/document/navigation?${parameters}`,
    { signal },
    validateDocumentNavigation,
  )
}

export function documentOcrLanguages(signal: AbortSignal) {
  return request(
    `${workspacePath}/files/document/ocr-languages`,
    { signal },
    (value: unknown): value is string[] =>
      Array.isArray(value) && value.every((item) => typeof item === 'string'),
  )
}

export function renderDocumentPage(
  path: string,
  folderId: string,
  unit: number,
  sha256: string,
  signal: AbortSignal,
) {
  const query = new URLSearchParams({ path, folder_id: folderId, unit: String(unit), sha256 })
  return requestBlob(`${workspacePath}/files/document/page?${query}`, { signal })
}
