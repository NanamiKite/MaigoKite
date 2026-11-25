// Inline controls disappear after resolution; do not leave keyboard focus on body.
export function awaitFocus(card: HTMLElement | null): () => void {
  const focused = document.activeElement
  if (!(focused instanceof HTMLElement) || !card?.contains(focused)) return () => {}
  const node = card.closest('[data-await-anchor]')?.querySelector<HTMLElement>('.node-button')
  const pending = card.closest('.pending-tab')
  return () => {
    if (
      focused.isConnected ||
      (document.activeElement !== document.body && document.activeElement?.isConnected)
    )
      return
    const targets = [
      node,
      pending?.querySelector<HTMLElement>('[data-await-action]:not(:disabled)'),
      pending
        ? document.querySelector<HTMLElement>(
            "#work-drawer .drawer-refresh:not(:disabled, [aria-disabled='true'])",
          )
        : undefined,
      document.querySelector<HTMLElement>('.composer textarea'),
    ]
    targets
      .find(
        (target) =>
          target?.isConnected && !target.closest('[inert]') && target.getClientRects().length,
      )
      ?.focus({ preventScroll: true })
  }
}

export function awaitCardVisible(card: HTMLElement | null): card is HTMLElement {
  if (!card) return false
  const target = card.querySelector<HTMLElement>('[data-await-action]:not(:disabled)') || card
  const rect = target.getBoundingClientRect()
  const area = rect.width * rect.height
  if (!area) return false
  const port = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight }
  for (let node = target.parentElement; node; node = node.parentElement) {
    const style = getComputedStyle(node)
    if (style.overflowX === 'visible' && style.overflowY === 'visible') continue
    const clip = node.getBoundingClientRect()
    port.left = Math.max(port.left, clip.left + node.clientLeft)
    port.top = Math.max(port.top, clip.top + node.clientTop)
    port.right = Math.min(port.right, clip.left + node.clientLeft + node.clientWidth)
    port.bottom = Math.min(port.bottom, clip.top + node.clientTop + node.clientHeight)
  }
  const width = Math.max(0, Math.min(rect.right, port.right) - Math.max(rect.left, port.left))
  const height = Math.max(0, Math.min(rect.bottom, port.bottom) - Math.max(rect.top, port.top))
  return (width * height) / area >= 0.5
}
