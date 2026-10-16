import { DESK_LIMITS } from './desk-preferences'

export class DeskImageError extends Error {}
export function checkDeskSize(width: number, height: number) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1)
    throw new DeskImageError('图片无法读取，请选择另一张。')
  if (Math.max(width, height) > DESK_LIMITS.dimension || width * height > DESK_LIMITS.pixels)
    throw new DeskImageError('图片过大，请先缩小图片。')
}

// Container metadata only, never a second image decoder. Check dimensions before
// createImageBitmap so a tiny compressed file cannot request an enormous bitmap.
export function inspectDeskImage(bytes: Uint8Array): { width: number; height: number } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const text = (at: number, size: number) => String.fromCharCode(...bytes.subarray(at, at + size))
  const animated = () => {
    throw new DeskImageError('暂不支持动图，请选择静态图片。')
  }
  let width = 0,
    height = 0
  try {
    if (text(1, 3) === 'PNG' && bytes[0] === 137 && view.getUint32(4) === 0x0d0a1a0a) {
      for (let at = 8; at + 12 <= bytes.length;) {
        const length = view.getUint32(at),
          kind = text(at + 4, 4)
        if (at + length + 12 > bytes.length) throw new Error()
        if (kind === 'acTL') animated()
        if (kind === 'IHDR') {
          width = view.getUint32(at + 8)
          height = view.getUint32(at + 12)
        }
        at += length + 12
      }
    } else if (bytes[0] === 255 && bytes[1] === 216) {
      for (let at = 2; at + 4 < bytes.length;) {
        if (bytes[at++] !== 255) throw new Error()
        while (bytes[at] === 255) at++
        const marker = bytes[at++]
        if (marker === 0xda || marker === 0xd9) break
        if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue
        const length = view.getUint16(at)
        if (length < 2 || at + length > bytes.length) throw new Error()
        if (
          [0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(
            marker,
          )
        ) {
          height = view.getUint16(at + 3)
          width = view.getUint16(at + 5)
        }
        at += length
      }
    } else if (text(0, 4) === 'RIFF' && text(8, 4) === 'WEBP') {
      for (let at = 12; at + 8 <= bytes.length;) {
        const kind = text(at, 4),
          length = view.getUint32(at + 4, true),
          start = at + 8
        if (start + length > bytes.length) throw new Error()
        if (kind === 'ANIM' || kind === 'ANMF') animated()
        if (kind === 'VP8X') {
          if (bytes[start] & 2) animated()
          const uint24 = (offset: number) =>
            bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16)
          width = 1 + uint24(start + 4)
          height = 1 + uint24(start + 7)
        } else if (!width && kind === 'VP8 ') {
          width = view.getUint16(start + 6, true) & 0x3fff
          height = view.getUint16(start + 8, true) & 0x3fff
        } else if (!width && kind === 'VP8L' && bytes[start] === 0x2f) {
          const bits = view.getUint32(start + 1, true)
          width = (bits & 0x3fff) + 1
          height = ((bits >>> 14) & 0x3fff) + 1
        }
        at = start + length + (length & 1)
      }
    } else if (text(4, 4) === 'ftyp') {
      const size = view.getUint32(0)
      if (size < 16 || size > bytes.length) throw new Error()
      const brands = [text(8, 4)]
      for (let at = 16; at < size; at += 4) brands.push(text(at, 4))
      if (brands.includes('avis') || brands.includes('msf1')) animated()
      if (!brands.includes('avif'))
        throw new DeskImageError('请选择 PNG、JPEG、WebP 或 AVIF 静态图片。')
      const boxes = (start: number, end: number, depth: number): void => {
        if (depth > 5) throw new Error()
        for (let at = start; at + 8 <= end;) {
          const length = view.getUint32(at),
            kind = text(at + 4, 4)
          if (length < 8 || at + length > end) throw new Error()
          if (kind === 'moov') animated()
          if (kind === 'ispe') {
            width = Math.max(width, view.getUint32(at + 12))
            height = Math.max(height, view.getUint32(at + 16))
          } else if (['meta', 'iprp', 'ipco'].includes(kind))
            boxes(at + (kind === 'meta' ? 12 : 8), at + length, depth + 1)
          at += length
        }
      }
      boxes(size, bytes.length, 0)
    } else throw new DeskImageError('请选择 PNG、JPEG、WebP 或 AVIF 静态图片。')
  } catch (error) {
    if (error instanceof DeskImageError) throw error
    throw new DeskImageError('图片无法读取，请选择另一张。')
  }
  checkDeskSize(width, height)
  return { width, height }
}
