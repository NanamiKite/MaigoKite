/** Folded logs still participate in minimap and keyboard navigation. */
export function revealRailNode(element: HTMLElement | null, block: ScrollLogicalPosition = 'center') {
  if (!element) return
  for (let parent = element.parentElement; parent; parent = parent.parentElement)
    if (parent instanceof HTMLDetailsElement) parent.open = true
  element.focus({ preventScroll: true })
  element.scrollIntoView({ block })
}
