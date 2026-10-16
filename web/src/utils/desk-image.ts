import { DESK_LIMITS } from './desk-preferences'
import { checkDeskSize, DeskImageError, inspectDeskImage } from './desk-image-header'
import type { RGB } from './color-contrast'

export interface DeskImage {
  id: string
  clear: Blob
  soft: Blob
  width: number
  height: number
}
const context = (canvas: OffscreenCanvas) => {
  const ctx = canvas.getContext('2d', { alpha: false, willReadFrequently: true })
  if (!ctx) throw new DeskImageError('当前环境无法处理图片。')
  return ctx
}

export async function prepareDeskImage(file: Blob, matte: RGB, blur: number): Promise<DeskImage> {
  if (file.size > DESK_LIMITS.bytes) throw new DeskImageError('图片过大，请先缩小图片。')
  inspectDeskImage(new Uint8Array(await file.arrayBuffer()))
  let bitmap: ImageBitmap | undefined
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
    checkDeskSize(bitmap.width, bitmap.height)
    const scale = Math.min(1, DESK_LIMITS.clearEdge / Math.max(bitmap.width, bitmap.height))
    const width = Math.max(1, Math.round(bitmap.width * scale)),
      height = Math.max(1, Math.round(bitmap.height * scale))
    const clear = new OffscreenCanvas(width, height),
      ctx = context(clear)
    // Flatten transparent imports onto the current paper once. Stored images
    // are opaque, so alpha cannot bypass the RGB contrast calculation later.
    ctx.fillStyle = `rgb(${matte.join(' ')})`
    ctx.fillRect(0, 0, width, height)
    ctx.drawImage(bitmap, 0, 0, width, height)
    const shrink = Math.min(1, DESK_LIMITS.softEdge / Math.max(width, height))
    const w = Math.max(1, Math.round(width * shrink)),
      h = Math.max(1, Math.round(height * shrink))
    const soft = new OffscreenCanvas(w, h),
      softCtx = context(soft)
    // Opaque enlarged underlay protects all four edges from blur transparency.
    const pad = Math.ceil(blur * 3)
    softCtx.drawImage(clear, -pad, -pad, w + pad * 2, h + pad * 2)
    softCtx.filter = `blur(${blur}px)`
    softCtx.drawImage(clear, 0, 0, w, h)
    softCtx.filter = 'none'
    const [clearBlob, softBlob] = await Promise.all([
      clear.convertToBlob({ type: 'image/webp', quality: 0.9 }),
      soft.convertToBlob({ type: 'image/webp', quality: 0.9 }),
    ])
    return { id: crypto.randomUUID(), clear: clearBlob, soft: softBlob, width, height }
  } catch (error) {
    if (error instanceof DeskImageError) throw error
    throw new DeskImageError('图片无法读取，请选择另一张。')
  } finally {
    bitmap?.close()
  }
}

export async function softPixels(blob: Blob): Promise<Uint8ClampedArray> {
  const bitmap = await createImageBitmap(blob)
  try {
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height),
      ctx = context(canvas)
    ctx.drawImage(bitmap, 0, 0)
    return ctx.getImageData(0, 0, canvas.width, canvas.height).data
  } finally {
    bitmap.close()
  }
}
