import { afterEach, describe, expect, it, vi } from 'vitest'
import { contrastRatio, type RGB } from '@/utils/color-contrast'
import {
  maxImageWeight,
  minimumContrasts,
  paperPixel,
  plateAlpha,
  quantizedColors,
  type DeskPalette,
} from '@/utils/desk-contrast'
import { DESK_LIMITS, deskPreferences, emptyDesk } from '@/utils/desk-preferences'
import { inspectDeskImage, checkDeskSize } from '@/utils/desk-image-header'
import { prepareDeskImage } from '@/utils/desk-image'
import { getDeskImage } from '@/utils/desk-storage'

const palette = (dark = false): DeskPalette => {
  const surfaces: RGB[] = dark
    ? [
        [20, 23, 22],
        [28, 33, 31],
      ]
    : [
        [248, 245, 239],
        [240, 236, 227],
      ]
  return {
    paper: dark ? [20, 23, 22] : [248, 245, 239],
    dim: 0.25,
    pairs: surfaces.flatMap((surface) => [
      { surface, ink: (dark ? [241, 238, 232] : [37, 39, 34]) as RGB, target: 7 },
      { surface, ink: (dark ? [144, 153, 141] : [98, 101, 92]) as RGB, target: 4.5 },
    ]),
  }
}
function png(width = 1, height = 1, animated = false) {
  const bytes = new Uint8Array(animated ? 53 : 33),
    view = new DataView(bytes.buffer)
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10])
  view.setUint32(8, 13)
  bytes.set([73, 72, 68, 82], 12)
  view.setUint32(16, width)
  view.setUint32(20, height)
  if (animated) {
    view.setUint32(33, 8)
    bytes.set([97, 99, 84, 76], 37)
  }
  return bytes
}
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('desk contrast', () => {
  it('leaves white-on-light and black-on-dark images unconstrained', () => {
    expect(maxImageWeight([[255, 255, 255]], palette())).toBe(1)
    expect(maxImageWeight([[0, 0, 0]], palette(true))).toBe(1)
  })
  it('visibility zero is opaque, with a separate physical dim limit', () => {
    expect(plateAlpha(0, 1, 0.25)).toBe(1)
    expect(plateAlpha(1, 1, 0.25)).toBe(0)
    expect(plateAlpha(1, 0, 0.25)).toBe(1)
    expect(plateAlpha(1, 1, 1)).toBe(1)
  })
  it('cannot increase its limit when an adverse color is added', () => {
    const colors: RGB[] = [
      [255, 255, 255],
      [180, 140, 100],
      [0, 0, 0],
    ]
    let prior = 1
    for (let i = 1; i <= colors.length; i++) {
      const limit = maxImageWeight(colors.slice(0, i), palette())
      expect(limit).toBeLessThanOrEqual(prior)
      prior = limit
    }
  })
  it('protects all real layers at every visibility in both themes', () => {
    const colors: RGB[] = [
      [0, 0, 0],
      [255, 255, 255],
      [255, 0, 0],
      [0, 255, 0],
      [0, 0, 255],
    ]
    for (const dark of [false, true]) {
      const p = palette(dark),
        max = maxImageWeight(colors, p)
      for (let step = 0; step <= 10; step++) {
        const ratios = minimumContrasts(colors, p, plateAlpha(step / 10, max, p.dim))
        expect(ratios.body).toBeGreaterThanOrEqual(7)
        expect(ratios.secondary).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
  it('composites paper-2 over the paper dim layer, not over itself', () => {
    const p = palette(),
      pair = p.pairs[2]
    expect(paperPixel([0, 0, 0], pair, p, 0)).toEqual([62, 61.25, 59.75])
    expect(paperPixel([0, 0, 0], pair, p, 1)).toEqual(pair.surface)
    expect(contrastRatio(pair.surface, pair.ink)).toBeGreaterThan(7)
  })
  it('quantizes actual colors, deduplicates, and never invents channel bounds', () => {
    expect(quantizedColors(new Uint8ClampedArray([0, 255, 0, 255, 1, 254, 1, 255]))).toEqual([
      [4, 252, 4],
    ])
    const bytes = new Uint8ClampedArray(32768 * 4)
    for (let i = 0; i < 32768; i++)
      bytes.set([(i >> 10) * 8, ((i >> 5) & 31) * 8, (i & 31) * 8, 255], i * 4)
    expect(quantizedColors(bytes)).toHaveLength(32768)
  })
})

describe('desk preferences and import failures', () => {
  it('validates versions, ranges, ids and strips unknown fields', () => {
    const valid = { ...emptyDesk(), on: true, imageId: 'local-image', extra: 'not persisted' }
    expect(deskPreferences(valid)).toEqual({ ...emptyDesk(), on: true, imageId: 'local-image' })
    for (const patch of [
      { v: 2 },
      { on: 'yes' },
      { visibility: Infinity },
      { visibility: -1 },
      { focal: { x: 0, y: 2 } },
      { imageId: 'https://remote/image' },
    ])
      expect(deskPreferences({ ...valid, ...patch })).toBeNull()
    expect(deskPreferences({ ...emptyDesk(), on: true })).toBeNull()
  })
  it('rejects illegal magic bytes and truncated containers', () => {
    expect(() => inspectDeskImage(new TextEncoder().encode('<svg/>'))).toThrow('请选择')
    expect(() => inspectDeskImage(png().slice(0, 20))).toThrow('无法读取')
    expect(inspectDeskImage(png(20, 30))).toEqual({ width: 20, height: 30 })
  })
  it('rejects animated PNG, WebP and AVIF containers', () => {
    expect(() => inspectDeskImage(png(20, 30, true))).toThrow('动图')
    const webp = new Uint8Array(20),
      avif = new Uint8Array(20)
    webp.set(new TextEncoder().encode('RIFF'), 0)
    webp.set(new TextEncoder().encode('WEBPANIM'), 8)
    new DataView(avif.buffer).setUint32(0, 20)
    avif.set(new TextEncoder().encode('ftypavis'), 4)
    expect(() => inspectDeskImage(webp)).toThrow('动图')
    expect(() => inspectDeskImage(avif)).toThrow('动图')
  })
  it('rejects 8K pixel count and oversize dimensions before decoding', () => {
    expect(() => inspectDeskImage(png(7680, 4320))).toThrow('请先缩小图片')
    expect(() => checkDeskSize(8193, 1)).toThrow('请先缩小图片')
    expect(() => checkDeskSize(0, 10)).toThrow('无法读取')
  })
  it('rejects oversized files without invoking a decoder', async () => {
    const decode = vi.fn()
    vi.stubGlobal('createImageBitmap', decode)
    const blob = new Blob()
    Object.defineProperty(blob, 'size', { value: DESK_LIMITS.bytes + 1 })
    await expect(prepareDeskImage(blob, [255, 255, 255], 8)).rejects.toThrow('请先缩小图片')
    expect(decode).not.toHaveBeenCalled()
  })
  it('reports corrupt files and releases decoded bitmaps on processing failure', async () => {
    const blob = new Blob()
    Object.defineProperty(blob, 'arrayBuffer', { value: async () => png().buffer })
    vi.stubGlobal('createImageBitmap', vi.fn().mockRejectedValue(new Error('decode')))
    await expect(prepareDeskImage(blob, [255, 255, 255], 8)).rejects.toThrow('无法读取')
    const close = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 1, height: 1, close }))
    vi.stubGlobal('OffscreenCanvas', undefined)
    await expect(prepareDeskImage(blob, [255, 255, 255], 8)).rejects.toThrow('无法读取')
    expect(close).toHaveBeenCalledOnce()
  })
  it('reports unavailable IndexedDB', async () => {
    vi.stubGlobal('indexedDB', undefined)
    await expect(getDeskImage('local-image')).rejects.toThrow('图片存储不可用')
  })
})
