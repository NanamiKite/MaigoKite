/** WCAG relative luminance of opaque, encoded sRGB channels in [0, 255]. */
export type RGB = readonly [number, number, number]

export function relativeLuminance(color: RGB): number {
  const linear = color.map((channel) => {
    const value = channel / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
}

export function contrastRatio(foreground: RGB, background: RGB): number {
  const first = relativeLuminance(foreground)
  const second = relativeLuminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

/** Matches color-mix(in srgb), not linear-light mixing. Used by offline token checks. */
export function mixSRGB(from: RGB, toward: RGB, weight: number): RGB {
  const amount = Math.max(0, Math.min(1, weight))
  return [
    from[0] * (1 - amount) + toward[0] * amount,
    from[1] * (1 - amount) + toward[1] * amount,
    from[2] * (1 - amount) + toward[2] * amount,
  ]
}
