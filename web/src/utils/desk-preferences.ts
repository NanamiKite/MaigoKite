export const DESK_LIMITS = Object.freeze({
  bytes: 20 * 1024 * 1024,
  dimension: 8192,
  pixels: 24_000_000,
  clearEdge: 2560,
  softEdge: 480,
})

export interface DeskPreferences {
  v: 1
  on: boolean
  imageId: string | null
  visibility: number
  // Optional for older v1 records; visibility supplies the initial slider value.
  paperTransparency?: number
  focal: { x: number; y: number }
}
export const emptyDesk = (): DeskPreferences => ({
  v: 1,
  on: false,
  imageId: null,
  visibility: 0.65,
  focal: { x: 0.5, y: 0.5 },
})
const unit = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1

export function deskPreferences(value: unknown): DeskPreferences | null {
  if (!value || typeof value !== 'object') return null
  const p = value as Record<string, unknown>
  const focal = p.focal as Record<string, unknown> | null
  if (
    p.v !== 1 ||
    typeof p.on !== 'boolean' ||
    !unit(p.visibility) ||
    (p.paperTransparency !== undefined && !unit(p.paperTransparency)) ||
    !(p.imageId === null || (typeof p.imageId === 'string' && /^[\w-]{1,80}$/.test(p.imageId))) ||
    (p.on && !p.imageId) ||
    !focal ||
    !unit(focal.x) ||
    !unit(focal.y)
  )
    return null
  // Copy only accepted fields; no URLs or binary data in preferences.
  return {
    v: 1,
    on: p.on,
    imageId: p.imageId,
    visibility: p.visibility,
    ...(p.paperTransparency === undefined ? {} : { paperTransparency: p.paperTransparency }),
    focal: { x: focal.x, y: focal.y },
  }
}
