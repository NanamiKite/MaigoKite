import type { RGB } from './color-contrast'
import type { DeskPalette } from './desk-contrast'

// Only genuine reading-paper surfaces; paper-3 and white-on-solid controls are opaque.
export const DESK_TEXT_TOKENS = [
  '--ink',
  '--ink-2',
  '--ink-3',
  '--accent-ink',
  '--accent-ink-action',
  '--wait-ink',
  '--seal',
  '--seal-ink-action',
  '--stone-ink',
  '--code-addition-ink',
  '--code-deletion-ink',
] as const

// The chat sheet has one paper surface; composer/usage inherit that plane.
// Opaque code and controls keep their own paper, not a fictitious translucent paper-3.
const CHAT_TEXT_TOKENS = [
  '--ink',
  '--desk-ink-2',
  '--desk-ink-3',
  '--desk-accent-ink',
  '--desk-wait-ink',
  '--desk-seal-ink',
  '--desk-stone-ink',
  '--code-addition-ink',
  '--code-deletion-ink',
] as const

export function readDeskPalette(root: HTMLElement, chat = false): DeskPalette {
  const style = getComputedStyle(root)
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('当前环境无法处理图片。')
  const probe = document.createElement('span')
  probe.hidden = true
  root.append(probe)
  const color = (token: string): RGB => {
    probe.style.color = `var(${token})`
    context.clearRect(0, 0, 1, 1)
    context.fillStyle = getComputedStyle(probe).color
    context.fillRect(0, 0, 1, 1)
    const pixel = context.getImageData(0, 0, 1, 1).data
    return [pixel[0], pixel[1], pixel[2]]
  }
  try {
    const paper = color('--paper'),
      second = color('--paper-2')
    return {
      paper,
      dim: Math.max(0, Math.min(1, Number(style.getPropertyValue('--desk-dim')))),
      pairs: (chat ? [paper] : [paper, second]).flatMap((surface) =>
        (chat ? CHAT_TEXT_TOKENS : DESK_TEXT_TOKENS).map((token) => ({
          surface,
          ink: color(token),
          target: token === '--ink' ? 7 : 4.5,
        })),
      ),
    }
  } finally {
    probe.remove()
  }
}

export function paletteKey(palette: DeskPalette): string {
  // A deterministic cache fingerprint, not a security hash.
  let hash = 2166136261
  for (const char of JSON.stringify(palette)) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619)
  return (hash >>> 0).toString(16)
}
