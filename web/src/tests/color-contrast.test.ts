import { describe, expect, it } from 'vitest'
import { contrastRatio, mixSRGB, relativeLuminance } from '@/utils/color-contrast'

describe('opaque sRGB contrast', () => {
  it('uses WCAG black/white endpoints and is symmetric', () => {
    expect(relativeLuminance([0, 0, 0])).toBe(0)
    expect(relativeLuminance([255, 255, 255])).toBe(1)
    expect(contrastRatio([0, 0, 0], [255, 255, 255])).toBe(21)
    expect(contrastRatio([255, 255, 255], [0, 0, 0])).toBe(21)
    expect(contrastRatio([80, 90, 100], [80, 90, 100])).toBe(1)
  })

  it('linearizes low and high sRGB values before computing luminance', () => {
    expect(relativeLuminance([10, 10, 10])).toBeCloseTo(10 / 255 / 12.92, 10)
    expect(relativeLuminance([128, 128, 128])).toBeCloseTo(0.2158605001, 9)
    expect(contrastRatio([119, 119, 119], [255, 255, 255])).toBeCloseTo(4.47808945, 7)
  })

  it('mixes in encoded sRGB, preserving endpoints and clamping the weight', () => {
    expect(mixSRGB([0, 0, 0], [255, 255, 255], 0.5)).toEqual([127.5, 127.5, 127.5])
    expect(mixSRGB([10, 20, 30], [200, 210, 220], -1)).toEqual([10, 20, 30])
    expect(mixSRGB([10, 20, 30], [200, 210, 220], 2)).toEqual([200, 210, 220])
  })

  it('improves contrast monotonically when mixing the light-theme ochre toward ink', () => {
    let previous = 0
    for (let step = 0; step <= 100; step++) {
      const ratio = contrastRatio(
        mixSRGB([151, 107, 67], [37, 39, 34], step / 100),
        [240, 236, 227],
      )
      expect(ratio).toBeGreaterThanOrEqual(previous)
      previous = ratio
    }
  })
})
