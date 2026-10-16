// Shared IPC data only: no Electron or renderer dependencies.
export interface GraphicsStatus {
  state: 'pending' | 'ready' | 'unavailable'
  enabledAtStartup: boolean
  preferredEnabled: boolean
  features: {
    compositing: string | null
    rasterization: string | null
    webgl: string | null
  }
}

export function graphicsStatusValue(value: unknown): value is GraphicsStatus {
  if (!value || typeof value !== 'object') return false
  const status = value as Record<string, unknown>
  if (!status.features || typeof status.features !== 'object') return false
  const features = status.features as Record<string, unknown>
  return (
    (status.state === 'pending' || status.state === 'ready' || status.state === 'unavailable') &&
    typeof status.enabledAtStartup === 'boolean' &&
    typeof status.preferredEnabled === 'boolean' &&
    ['compositing', 'rasterization', 'webgl'].every(
      (key) =>
        features[key] === null ||
        (typeof features[key] === 'string' && features[key].length <= 100),
    )
  )
}
