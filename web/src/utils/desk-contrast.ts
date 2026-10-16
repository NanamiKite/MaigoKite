import { contrastRatio, mixSRGB, relativeLuminance, type RGB } from './color-contrast'

export interface DeskPair {
  surface: RGB
  ink: RGB
  target: number
}
export interface DeskPalette {
  paper: RGB
  dim: number
  pairs: DeskPair[]
}
export const CONTRAST_MARGIN = 1.03

// RGB5 deduplication is performed on the actual, opaque softened bitmap only.
export function quantizedColors(pixels: Uint8ClampedArray): RGB[] {
  const seen = new Set<number>()
  for (let i = 0; i < pixels.length; i += 4)
    seen.add(((pixels[i] >> 3) << 10) | ((pixels[i + 1] >> 3) << 5) | (pixels[i + 2] >> 3))
  return [...seen].map((key) => [
    Math.min(255, ((key >> 10) << 3) + 4),
    Math.min(255, (((key >> 5) & 31) << 3) + 4),
    Math.min(255, ((key & 31) << 3) + 4),
  ])
}

export function plateAlpha(visibility: number, maximum: number, dim: number): number {
  if (dim >= 1) return 1
  const weight = Math.max(0, Math.min(1, visibility)) * Math.min(maximum, 1 - dim)
  return 1 - weight / (1 - dim)
}

export function paperPixel(image: RGB, pair: DeskPair, palette: DeskPalette, alpha: number): RGB {
  return mixSRGB(mixSRGB(image, palette.paper, palette.dim), pair.surface, alpha)
}

// A segment's luminance is convex in encoded sRGB. For dark ink, a mixed-hue
// image can have a darker interior than either endpoint; check that minimum too.
function safeSegment(from: RGB, to: RGB, ink: RGB, target: number): boolean {
  if (contrastRatio(to, ink) < target) return false
  const inkLight = relativeLuminance(ink),
    fromLight = relativeLuminance(from),
    toLight = relativeLuminance(to)
  // Safe endpoints on opposite sides of the ink still cross zero contrast.
  if ((fromLight - inkLight) * (toLight - inkLight) <= 0) return false
  if (fromLight <= inkLight) return true
  const delta = to.map((v, i) => v - from[i])
  if (delta.every((v) => v >= 0) || delta.every((v) => v <= 0)) return true
  let low = 0,
    high = 1
  for (let i = 0; i < 14; i++) {
    const a = low + (high - low) / 3,
      b = high - (high - low) / 3
    if (relativeLuminance(mixSRGB(from, to, a)) < relativeLuminance(mixSRGB(from, to, b))) high = b
    else low = a
  }
  return contrastRatio(mixSRGB(from, to, (low + high) / 2), ink) >= target
}

/** Maximum safe image weight; never increases it for a later, more adverse color. */
export function maxImageWeight(
  colors: RGB[],
  palette: DeskPalette,
  margin = CONTRAST_MARGIN,
): number {
  let maximum = 1 - palette.dim
  for (const pair of palette.pairs) {
    const target = pair.target * margin
    if (contrastRatio(pair.surface, pair.ink) < target) return 0
    for (const color of colors) {
      const safe = (weight: number) =>
        safeSegment(
          pair.surface,
          paperPixel(color, pair, palette, plateAlpha(1, weight, palette.dim)),
          pair.ink,
          target,
        )
      if (safe(maximum)) continue
      let low = 0,
        high = maximum
      for (let i = 0; i < 20; i++) {
        const mid = (low + high) / 2
        if (safe(mid)) low = mid
        else high = mid
      }
      maximum = low
    }
  }
  // An unconstrained image has a limit of 1; the dim layer's physical cap is
  // applied by plateAlpha, independently of readability.
  return maximum === 1 - palette.dim ? 1 : Math.max(0, maximum)
}

export function minimumContrasts(colors: RGB[], palette: DeskPalette, alpha: number) {
  let body = Infinity,
    secondary = Infinity
  for (const pair of palette.pairs)
    for (const color of colors) {
      const ratio = contrastRatio(pair.ink, paperPixel(color, pair, palette, alpha))
      if (pair.target === 7) body = Math.min(body, ratio)
      else secondary = Math.min(secondary, ratio)
    }
  return { body, secondary }
}
